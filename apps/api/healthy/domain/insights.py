from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

INSIGHTS_RULE_VERSION = "evidence-linked-insights-v1"
# Presentation/product bound only; this is not a medical threshold.
MAX_INSIGHTS = 5
_INSIGHT_NAMESPACE = uuid.UUID("c4c75f7f-5f8c-4b96-a9bb-16bc0c3e4d6b")

InsightType = Literal["metric_change", "symptom_pattern", "report_observation_update"]
SourceKind = Literal["metric", "symptom", "report_observation"]


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
    symptom: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ReportObservationSnapshot:
    id: uuid.UUID
    report_id: uuid.UUID
    report_source_name: str
    code: str
    display_name: str
    value_numeric: Decimal | None
    value_text: str | None
    unit: str | None
    observed_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    source_kind: SourceKind
    source_record_id: uuid.UUID
    occurred_at: datetime
    role: str | None = None
    report_id: uuid.UUID | None = None
    report_source_name: str | None = None


@dataclass(frozen=True, slots=True)
class Insight:
    id: uuid.UUID
    insight_type: InsightType
    headline: str
    observed_at: datetime
    evidence: tuple[EvidenceReference, ...]


_METRIC_FIELDS: tuple[tuple[str, str, str | None], ...] = (
    ("systolic_bp_mm_hg", "Systolic blood pressure", "mmHg"),
    ("diastolic_bp_mm_hg", "Diastolic blood pressure", "mmHg"),
    ("heart_rate_bpm", "Heart rate", "bpm"),
    ("weight_kg", "Weight", "kg"),
    ("blood_glucose_mg_dl", "Blood glucose", "mg/dL"),
)


def _is_chronologically_valid(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _chronological_key(value: datetime, record_id: uuid.UUID) -> tuple[datetime, str]:
    return value.astimezone(UTC), str(record_id)


def _format_value(value: int | Decimal | str) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def _with_unit(value: int | Decimal | str, unit: str | None) -> str:
    formatted = _format_value(value)
    return f"{formatted} {unit}" if unit else formatted


def _stable_id(insight_type: InsightType, identity: str) -> uuid.UUID:
    return uuid.uuid5(_INSIGHT_NAMESPACE, f"{insight_type}|{identity}")


def _metric_value(metric: MetricSnapshot, field_name: str) -> int | Decimal | None:
    values: dict[str, int | Decimal | None] = {
        "systolic_bp_mm_hg": metric.systolic_bp_mm_hg,
        "diastolic_bp_mm_hg": metric.diastolic_bp_mm_hg,
        "heart_rate_bpm": metric.heart_rate_bpm,
        "weight_kg": metric.weight_kg,
        "blood_glucose_mg_dl": metric.blood_glucose_mg_dl,
    }
    return values[field_name]


def _metric_change_insights(metrics: list[MetricSnapshot]) -> list[Insight]:
    insights: list[Insight] = []
    for field_name, display_name, unit in _METRIC_FIELDS:
        observations = [
            metric
            for metric in metrics
            if _is_chronologically_valid(metric.recorded_at)
            and _metric_value(metric, field_name) is not None
        ]
        observations.sort(key=lambda metric: _chronological_key(metric.recorded_at, metric.id))
        if len(observations) < 2:
            continue

        previous, latest = observations[-2:]
        previous_value = _metric_value(previous, field_name)
        latest_value = _metric_value(latest, field_name)
        assert previous_value is not None
        assert latest_value is not None
        evidence = (
            EvidenceReference(
                source_kind="metric",
                source_record_id=previous.id,
                occurred_at=previous.recorded_at,
                role="previous",
            ),
            EvidenceReference(
                source_kind="metric",
                source_record_id=latest.id,
                occurred_at=latest.recorded_at,
                role="latest",
            ),
        )
        insights.append(
            Insight(
                id=_stable_id(
                    "metric_change",
                    f"{field_name}|{previous.id}|{latest.id}",
                ),
                insight_type="metric_change",
                headline=(
                    f"{display_name} changed from {_with_unit(previous_value, unit)} "
                    f"to {_with_unit(latest_value, unit)}."
                ),
                observed_at=latest.recorded_at,
                evidence=evidence,
            )
        )
    return insights


def _symptom_pattern_insights(symptoms: list[SymptomSnapshot]) -> list[Insight]:
    grouped: dict[str, list[SymptomSnapshot]] = {}
    for symptom in symptoms:
        if _is_chronologically_valid(symptom.occurred_at):
            grouped.setdefault(symptom.symptom.casefold(), []).append(symptom)

    insights: list[Insight] = []
    for records in grouped.values():
        records.sort(key=lambda symptom: _chronological_key(symptom.occurred_at, symptom.id))
        if len(records) < 2:
            continue
        latest = records[-1]
        evidence = tuple(
            EvidenceReference(
                source_kind="symptom",
                source_record_id=record.id,
                occurred_at=record.occurred_at,
                role="contributing",
            )
            for record in records
        )
        insights.append(
            Insight(
                id=_stable_id(
                    "symptom_pattern",
                    f"{latest.symptom.casefold()}|{','.join(str(record.id) for record in records)}",
                ),
                insight_type="symptom_pattern",
                headline=(f"{latest.symptom} appears in {len(records)} recorded symptom entries."),
                observed_at=latest.occurred_at,
                evidence=evidence,
            )
        )
    return insights


def _report_observation_value(observation: ReportObservationSnapshot) -> str:
    if observation.value_numeric is not None:
        return _with_unit(observation.value_numeric, observation.unit)
    return _with_unit(observation.value_text or "", observation.unit)


def _report_observation_insights(
    observations: list[ReportObservationSnapshot],
) -> list[Insight]:
    grouped: dict[str, list[ReportObservationSnapshot]] = {}
    for observation in observations:
        if _is_chronologically_valid(observation.observed_at):
            grouped.setdefault(observation.code.casefold(), []).append(observation)

    insights: list[Insight] = []
    for records in grouped.values():
        records.sort(
            key=lambda observation: (
                observation.observed_at.astimezone(UTC),
                observation.created_at.astimezone(UTC),
                str(observation.id),
            )
        )
        latest = records[-1]
        evidence = (
            EvidenceReference(
                source_kind="report_observation",
                source_record_id=latest.id,
                occurred_at=latest.observed_at,
                role="latest",
                report_id=latest.report_id,
                report_source_name=latest.report_source_name,
            ),
        )
        insights.append(
            Insight(
                id=_stable_id("report_observation_update", f"{latest.code}|{latest.id}"),
                insight_type="report_observation_update",
                headline=(
                    f"Latest confirmed report records {latest.display_name}: "
                    f"{_report_observation_value(latest)}."
                ),
                observed_at=latest.observed_at,
                evidence=evidence,
            )
        )
    return insights


def build_insights(
    *,
    metrics: list[MetricSnapshot],
    symptoms: list[SymptomSnapshot],
    confirmed_report_observations: list[ReportObservationSnapshot],
) -> tuple[Insight, ...]:
    """Build descriptive insights from already-loaded, owner-scoped records."""
    candidates = (
        _metric_change_insights(metrics)
        + _symptom_pattern_insights(symptoms)
        + _report_observation_insights(confirmed_report_observations)
    )
    candidates.sort(
        key=lambda insight: (
            -insight.observed_at.astimezone(UTC).timestamp(),
            insight.insight_type,
            str(insight.id),
        )
    )
    return tuple(candidates[:MAX_INSIGHTS])
