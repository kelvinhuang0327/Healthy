from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

RULE_VERSION = "deterministic-health-score-v1"
LOOKBACK_DAYS = 14
LOOKBACK = timedelta(days=LOOKBACK_DAYS)
MAX_EVIDENCE_IDS = 5

HealthScoreStatus = Literal["stable", "monitor", "attention", "insufficient_data"]
ComponentKind = Literal["blood_pressure", "heart_rate", "blood_glucose", "recent_symptoms"]


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    id: uuid.UUID
    recorded_at: datetime
    systolic_bp_mm_hg: int | None
    diastolic_bp_mm_hg: int | None
    heart_rate_bpm: int | None
    weight_kg: Decimal | None
    blood_glucose_mg_dl: Decimal | None


@dataclass(frozen=True, slots=True)
class SymptomSnapshot:
    id: uuid.UUID
    occurred_at: datetime
    severity: int


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    kind: ComponentKind
    label: str
    points: int
    penalty: int
    evidence_ids: tuple[uuid.UUID, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class HealthScore:
    score: int | None
    status: HealthScoreStatus
    rule_version: str
    anchor_at: datetime | None
    data_points: int
    components: tuple[ScoreComponent, ...]
    limitations: str


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _penalty(value: int | Decimal, bands: tuple[tuple[int | Decimal, int], ...]) -> int:
    for upper_bound, penalty in bands:
        if value <= upper_bound:
            return penalty
    return bands[-1][1]


def _metric_sort_key(metric: MetricSnapshot) -> tuple[datetime, str]:
    return _utc(metric.recorded_at), str(metric.id)


def _symptom_sort_key(symptom: SymptomSnapshot) -> tuple[datetime, str]:
    return _utc(symptom.occurred_at), str(symptom.id)


def _blood_pressure_component(metric: MetricSnapshot) -> ScoreComponent | None:
    if metric.systolic_bp_mm_hg is None or metric.diastolic_bp_mm_hg is None:
        return None
    systolic_penalty = _penalty(
        metric.systolic_bp_mm_hg,
        ((120, 0), (139, 10), (159, 20), (300, 35)),
    )
    diastolic_penalty = _penalty(
        metric.diastolic_bp_mm_hg,
        ((80, 0), (89, 10), (99, 20), (200, 35)),
    )
    penalty = max(systolic_penalty, diastolic_penalty)
    return ScoreComponent(
        kind="blood_pressure",
        label="Blood pressure",
        points=100 - penalty,
        penalty=penalty,
        evidence_ids=(metric.id,),
        rationale=(
            f"Latest recorded blood pressure is {metric.systolic_bp_mm_hg}/"
            f"{metric.diastolic_bp_mm_hg} mmHg."
        ),
    )


def _heart_rate_component(metric: MetricSnapshot) -> ScoreComponent | None:
    if metric.heart_rate_bpm is None:
        return None
    penalty = _penalty(
        metric.heart_rate_bpm,
        ((49, 20), (59, 10), (100, 0), (110, 10), (300, 20)),
    )
    return ScoreComponent(
        kind="heart_rate",
        label="Heart rate",
        points=100 - penalty,
        penalty=penalty,
        evidence_ids=(metric.id,),
        rationale=f"Latest recorded heart rate is {metric.heart_rate_bpm} bpm.",
    )


def _blood_glucose_component(metric: MetricSnapshot) -> ScoreComponent | None:
    if metric.blood_glucose_mg_dl is None:
        return None
    penalty = _penalty(
        metric.blood_glucose_mg_dl,
        ((Decimal("69"), 15), (Decimal("140"), 0), (Decimal("180"), 15), (Decimal("1000"), 25)),
    )
    return ScoreComponent(
        kind="blood_glucose",
        label="Blood glucose",
        points=100 - penalty,
        penalty=penalty,
        evidence_ids=(metric.id,),
        rationale=(f"Latest recorded blood glucose is {metric.blood_glucose_mg_dl} mg/dL."),
    )


def _symptoms_component(
    symptoms: list[SymptomSnapshot],
    anchor_at: datetime,
) -> ScoreComponent | None:
    recent = [
        symptom for symptom in symptoms if _utc(symptom.occurred_at) >= _utc(anchor_at) - LOOKBACK
    ]
    if not recent:
        return None
    recent.sort(key=_symptom_sort_key)
    penalty = min(40, sum(symptom.severity * 2 for symptom in recent))
    return ScoreComponent(
        kind="recent_symptoms",
        label="Recent symptoms",
        points=100 - penalty,
        penalty=penalty,
        evidence_ids=tuple(symptom.id for symptom in recent[-MAX_EVIDENCE_IDS:]),
        rationale=(
            f"{len(recent)} symptom record(s) fall within the {LOOKBACK_DAYS}-day "
            "data window anchored to the latest record."
        ),
    )


def _status(score: int) -> HealthScoreStatus:
    if score >= 85:
        return "stable"
    if score >= 65:
        return "monitor"
    return "attention"


def build_health_score(
    *,
    metrics: list[MetricSnapshot],
    symptoms: list[SymptomSnapshot],
) -> HealthScore:
    """Build a transparent, non-diagnostic score from owner-scoped records.

    Only the latest values for each available metric are scored. Weight is retained
    as a record but is not scored because V1 has no height, age, or personal baseline.
    The anchor and tie-breakers are data-derived so repeated reads are stable.
    """
    valid_metrics = [metric for metric in metrics if metric.recorded_at.tzinfo is not None]
    valid_symptoms = [symptom for symptom in symptoms if symptom.occurred_at.tzinfo is not None]
    timestamps = [
        *(_utc(metric.recorded_at) for metric in valid_metrics),
        *(_utc(symptom.occurred_at) for symptom in valid_symptoms),
    ]
    if not timestamps:
        return HealthScore(
            score=None,
            status="insufficient_data",
            rule_version=RULE_VERSION,
            anchor_at=None,
            data_points=0,
            components=(),
            limitations=(
                "Add a blood pressure, heart rate, blood glucose, or symptom record. "
                "This score is a non-diagnostic product signal, not medical advice."
            ),
        )

    anchor_at = max(timestamps)
    latest_metric = max(valid_metrics, key=_metric_sort_key) if valid_metrics else None
    components: list[ScoreComponent] = []
    if latest_metric is not None:
        for component in (
            _blood_pressure_component(latest_metric),
            _heart_rate_component(latest_metric),
            _blood_glucose_component(latest_metric),
        ):
            if component is not None:
                components.append(component)
    symptom_component = _symptoms_component(valid_symptoms, anchor_at)
    if symptom_component is not None:
        components.append(symptom_component)

    if not components:
        return HealthScore(
            score=None,
            status="insufficient_data",
            rule_version=RULE_VERSION,
            anchor_at=anchor_at,
            data_points=len(valid_metrics) + len(valid_symptoms),
            components=(),
            limitations=(
                "Weight is shown in the metric history but is not scored in V1 without "
                "personal context. This score is a non-diagnostic product signal."
            ),
        )

    score = round(sum(component.points for component in components) / len(components))
    return HealthScore(
        score=score,
        status=_status(score),
        rule_version=RULE_VERSION,
        anchor_at=anchor_at,
        data_points=len(valid_metrics) + len(valid_symptoms),
        components=tuple(components),
        limitations=(
            "V1 scores only the latest available vital values and recent symptom severity. "
            "It is a non-diagnostic product signal, not medical advice."
        ),
    )
