from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

RULE_VERSION = "deterministic-health-score-v1"
LOOKBACK_DAYS = 30
LOOKBACK = timedelta(days=LOOKBACK_DAYS)

HealthScoreStatus = Literal["stable", "monitor", "attention", "insufficient_data"]
ComponentKind = Literal["cardiovascular", "metabolic", "activity", "weight", "overall"]


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    id: uuid.UUID
    recorded_at: datetime
    systolic_bp_mm_hg: int | None
    diastolic_bp_mm_hg: int | None
    heart_rate_bpm: int | None
    weight_kg: Decimal | None
    blood_glucose_mg_dl: Decimal | None
    created_at: datetime | None = None
    steps: int | None = None
    sleep_hours: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SymptomSnapshot:
    """Compatibility snapshot for callers of the first PR draft.

    The legacy score does not use symptom severity.  It uses the persisted
    estimated duration fact, represented by SymptomDurationSnapshot below.
    """

    id: uuid.UUID
    occurred_at: datetime
    severity: int
    estimated_duration_days: int | None = None


@dataclass(frozen=True, slots=True)
class NamedLabSnapshot:
    value: Decimal
    evidence_ids: tuple[uuid.UUID, ...]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class RiskAlertSnapshot:
    evidence_ids: tuple[uuid.UUID, ...]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class SymptomDurationSnapshot:
    id: uuid.UUID
    occurred_at: datetime
    estimated_duration_days: int


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
    score: int
    status: HealthScoreStatus
    rule_version: str
    anchor_at: datetime | None
    data_points: int
    components: tuple[ScoreComponent, ...]
    limitations: str


@dataclass(frozen=True, slots=True)
class _CategoryDetail:
    base_score: float
    evidence_ids: tuple[uuid.UUID, ...]
    context: Mapping[str, float | None]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("score inputs must have timezone-aware timestamps")
    return value.astimezone(UTC)


def _metric_sort_key(metric: MetricSnapshot) -> tuple[datetime, datetime, str]:
    return (
        _utc(metric.recorded_at),
        _utc(metric.created_at or metric.recorded_at),
        str(metric.id),
    )


def _symptom_sort_key(symptom: SymptomDurationSnapshot) -> tuple[datetime, str]:
    return _utc(symptom.occurred_at), str(symptom.id)


def _unique_ids(values: Iterable[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    seen: set[uuid.UUID] = set()
    result: list[uuid.UUID] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _avg(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _clamp_int(value: int) -> int:
    return max(0, min(100, int(value)))


def _score_cardiovascular(metrics: list[MetricSnapshot]) -> _CategoryDetail:
    score = 100.0
    systolic_values = [
        float(metric.systolic_bp_mm_hg)
        for metric in metrics
        if metric.systolic_bp_mm_hg is not None
    ]
    diastolic_values = [
        float(metric.diastolic_bp_mm_hg)
        for metric in metrics
        if metric.diastolic_bp_mm_hg is not None
    ]
    avg_systolic = _avg(systolic_values)
    avg_diastolic = _avg(diastolic_values)
    if avg_systolic is not None and avg_systolic > 120:
        score -= min(25, (avg_systolic - 120) * 0.8)
    if avg_diastolic is not None and avg_diastolic > 80:
        score -= min(20, (avg_diastolic - 80) * 1.0)
    evidence_ids = _unique_ids(
        metric.id
        for metric in metrics
        if metric.systolic_bp_mm_hg is not None or metric.diastolic_bp_mm_hg is not None
    )
    return _CategoryDetail(
        base_score=score,
        evidence_ids=evidence_ids,
        context={"avg_systolic": avg_systolic, "avg_diastolic": avg_diastolic},
    )


def _score_metabolic(
    metrics: list[MetricSnapshot],
    named_labs: Mapping[str, NamedLabSnapshot | None],
) -> _CategoryDetail:
    score = 100.0
    glucose_values = [
        float(metric.blood_glucose_mg_dl)
        for metric in metrics
        if metric.blood_glucose_mg_dl is not None
    ]
    avg_glucose = _avg(glucose_values)
    if avg_glucose is not None and avg_glucose > 99:
        score -= min(35, (avg_glucose - 99) * 0.8)

    latest_lipids = {
        key: (float(value.value) if value is not None else None)
        for key, value in named_labs.items()
    }
    total_cholesterol = latest_lipids.get("Total Cholesterol")
    ldl = latest_lipids.get("LDL")
    alt = latest_lipids.get("ALT")
    if total_cholesterol is not None and total_cholesterol > 200:
        score -= min(15, (total_cholesterol - 200) * 0.15)
    if ldl is not None and ldl > 130:
        score -= min(20, (ldl - 130) * 0.25)

    evidence_ids = _unique_ids(
        [
            *(metric.id for metric in metrics if metric.blood_glucose_mg_dl is not None),
            *(
                evidence_id
                for value in named_labs.values()
                if value is not None
                for evidence_id in value.evidence_ids
            ),
        ]
    )
    return _CategoryDetail(
        base_score=score,
        evidence_ids=evidence_ids,
        context={
            "avg_glucose": avg_glucose,
            "alt_above_ref": 1.0 if alt is not None and alt > 40 else 0.0,
        },
    )


def _calculate_bmi(weight_kg: Decimal | None, height_cm: Decimal | None) -> float | None:
    if weight_kg is None or height_cm is None:
        return None
    height_m = float(height_cm) / 100
    if height_m <= 0:
        return None
    return float(weight_kg) / (height_m * height_m)


def _score_weight(metrics: list[MetricSnapshot], height_cm: Decimal | None) -> _CategoryDetail:
    score = 100.0
    latest_weight_metric = next(
        (metric for metric in metrics if metric.weight_kg is not None),
        None,
    )
    latest_weight = latest_weight_metric.weight_kg if latest_weight_metric is not None else None
    bmi = _calculate_bmi(latest_weight, height_cm)
    if bmi is not None:
        if bmi < 18.5:
            score -= min(30, (18.5 - bmi) * 4)
        elif bmi > 24:
            score -= min(35, (bmi - 24) * 4)
    return _CategoryDetail(
        base_score=score,
        evidence_ids=(latest_weight_metric.id,) if latest_weight_metric is not None else (),
        context={"bmi": bmi},
    )


def _score_activity(metrics: list[MetricSnapshot]) -> _CategoryDetail:
    score = 100.0
    sleep_values = [
        float(metric.sleep_hours) for metric in metrics if metric.sleep_hours is not None
    ]
    step_values = [float(metric.steps) for metric in metrics if metric.steps is not None]
    avg_sleep = _avg(sleep_values)
    avg_steps = _avg(step_values)
    if avg_sleep is not None and avg_sleep < 7:
        score -= min(40, (7 - avg_sleep) * 12)
    if avg_steps is not None and avg_steps < 5000:
        score -= min(20, (5000 - avg_steps) / 300)
    evidence_ids = _unique_ids(
        metric.id
        for metric in metrics
        if metric.sleep_hours is not None or metric.steps is not None
    )
    return _CategoryDetail(
        base_score=score,
        evidence_ids=evidence_ids,
        context={"avg_sleep_hours": avg_sleep, "avg_steps": avg_steps},
    )


def _status(score: int) -> HealthScoreStatus:
    if score >= 85:
        return "stable"
    if score >= 65:
        return "monitor"
    return "attention"


def _format_value(value: float | None) -> str:
    return "missing" if value is None else f"{value:.2f}"


def _component(
    *,
    kind: ComponentKind,
    label: str,
    score: int,
    evidence_ids: tuple[uuid.UUID, ...],
    rationale: str,
) -> ScoreComponent:
    return ScoreComponent(
        kind=kind,
        label=label,
        points=score,
        penalty=100 - score,
        evidence_ids=evidence_ids,
        rationale=rationale,
    )


def build_health_score(
    *,
    metrics: Iterable[MetricSnapshot],
    now: datetime,
    named_labs: Mapping[str, NamedLabSnapshot | None] | None = None,
    risk_alerts: Iterable[RiskAlertSnapshot] = (),
    symptom_durations: Iterable[SymptomDurationSnapshot] = (),
    height_cm: Decimal | None = None,
    lookback_days: int = LOOKBACK_DAYS,
    symptoms: Iterable[SymptomSnapshot] | None = None,
) -> HealthScore:
    """Port the immutable PersonalHealthOS score contract into Healthy.

    The formula, thresholds, weights, rounding, rule penalties, and missing
    behavior below mirror commit 684a19dbc2667d8924873af40835aa89c144e4c0.
    Healthy supplies only person-scoped persisted facts; this function never
    writes derived score state.
    """
    if lookback_days < 0:
        raise ValueError("lookback_days must not be negative")
    normalized_now = _utc(now)
    start = normalized_now - timedelta(days=lookback_days)
    valid_metrics = sorted(
        [metric for metric in metrics if _utc(metric.recorded_at) >= start],
        key=_metric_sort_key,
        reverse=True,
    )
    labs = named_labs or {}
    alerts = tuple(risk_alerts)
    duration_rows = list(symptom_durations)
    if symptoms is not None:
        duration_rows.extend(
            SymptomDurationSnapshot(
                id=symptom.id,
                occurred_at=symptom.occurred_at,
                estimated_duration_days=symptom.estimated_duration_days,
            )
            for symptom in symptoms
            if symptom.estimated_duration_days is not None
        )
    duration_rows.sort(key=_symptom_sort_key, reverse=True)

    cardiovascular = _score_cardiovascular(valid_metrics)
    metabolic = _score_metabolic(valid_metrics, labs)
    activity = _score_activity(valid_metrics)
    weight = _score_weight(valid_metrics, height_cm)

    avg_systolic = cardiovascular.context["avg_systolic"]
    bmi = weight.context["bmi"]
    alt_above_ref = metabolic.context["alt_above_ref"] == 1.0
    avg_sleep = activity.context["avg_sleep_hours"]
    avg_steps = activity.context["avg_steps"]
    long_term_rows = [row for row in duration_rows if row.estimated_duration_days >= 180]

    cardiovascular_rule_penalty = 12 if avg_systolic is not None and avg_systolic > 140 else 0
    activity_rule_penalty = 0
    if bmi is not None and bmi > 27:
        activity_rule_penalty += 10
    if avg_sleep is not None and avg_sleep < 6.5:
        activity_rule_penalty += 8
    if avg_steps is not None and avg_steps < 5000:
        activity_rule_penalty += 6
    metabolic_rule_penalty = 12 if alt_above_ref else 0
    long_term_penalty = 8 if long_term_rows else 0
    risk_alert_penalty = min(15, len(alerts) * 3)
    overall_penalty = long_term_penalty + risk_alert_penalty

    cardiovascular_score = _clamp_int(
        round(cardiovascular.base_score - cardiovascular_rule_penalty)
    )
    metabolic_score = _clamp_int(round(metabolic.base_score - metabolic_rule_penalty))
    activity_score = _clamp_int(round(activity.base_score - activity_rule_penalty))
    weight_score = _clamp_int(round(weight.base_score))
    overall_score = _clamp_int(
        round(
            0.4 * cardiovascular_score
            + 0.35 * metabolic_score
            + 0.25 * activity_score
            - overall_penalty
        )
    )

    alert_evidence_ids = _unique_ids(
        evidence_id for alert in alerts for evidence_id in alert.evidence_ids
    )
    duration_evidence_ids = tuple(row.id for row in long_term_rows)
    overall_evidence_ids = _unique_ids([*alert_evidence_ids, *duration_evidence_ids])
    overall_rationale = (
        f"Legacy overall weighting is 40% cardiovascular, 35% metabolic, and 25% activity. "
        f"Applied {overall_penalty} overall point(s): {len(alerts)} active risk alert(s) "
        f"and {len(long_term_rows)} long-term symptom record(s)."
    )

    components = (
        _component(
            kind="cardiovascular",
            label="Cardiovascular",
            score=cardiovascular_score,
            evidence_ids=cardiovascular.evidence_ids,
            rationale=(
                f"Average blood pressure inputs: systolic {_format_value(avg_systolic)} mmHg, "
                f"diastolic {_format_value(cardiovascular.context['avg_diastolic'])} mmHg; "
                f"rule penalties applied: {cardiovascular_rule_penalty}."
            ),
        ),
        _component(
            kind="metabolic",
            label="Metabolic",
            score=metabolic_score,
            evidence_ids=metabolic.evidence_ids,
            rationale=(
                f"Average glucose {_format_value(metabolic.context['avg_glucose'])} mg/dL; "
                f"named-lab and ALT rule penalties applied: "
                f"{metabolic_rule_penalty}."
            ),
        ),
        _component(
            kind="activity",
            label="Activity and sleep",
            score=activity_score,
            evidence_ids=_unique_ids([*activity.evidence_ids, *weight.evidence_ids]),
            rationale=(
                f"Average sleep {_format_value(avg_sleep)} hours and steps "
                f"{_format_value(avg_steps)}; BMI/activity rule penalties applied: "
                f"{activity_rule_penalty}."
            ),
        ),
        _component(
            kind="weight",
            label="Weight",
            score=weight_score,
            evidence_ids=weight.evidence_ids,
            rationale=(
                f"Weight score uses the latest person-owned weight and profile height; "
                f"BMI {_format_value(bmi)}."
            ),
        ),
        ScoreComponent(
            kind="overall",
            label="Overall rule adjustments",
            points=_clamp_int(100 - overall_penalty),
            penalty=overall_penalty,
            evidence_ids=overall_evidence_ids,
            rationale=overall_rationale,
        ),
    )

    timestamps = [
        *(_utc(metric.recorded_at) for metric in valid_metrics),
        *(_utc(value.observed_at) for value in labs.values() if value is not None),
        *(_utc(row.occurred_at) for row in duration_rows),
        *(_utc(alert.observed_at) for alert in alerts),
    ]
    present_lab_count = sum(value is not None for value in labs.values())
    data_points = len(valid_metrics) + present_lab_count + len(duration_rows)
    return HealthScore(
        score=overall_score,
        status=_status(overall_score),
        rule_version=RULE_VERSION,
        anchor_at=max(timestamps) if timestamps else None,
        data_points=data_points,
        components=components,
        limitations=(
            "Missing legacy inputs receive no penalty. This is a deterministic, "
            "non-diagnostic product signal, not medical advice."
        ),
    )
