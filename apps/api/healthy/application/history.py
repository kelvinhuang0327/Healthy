from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from healthy.infrastructure.models import (
    HealthMetric,
    HealthReportObservationModel,
    SymptomLog,
)

HistoryKind = Literal["symptom", "metric", "report_observation"]


@dataclass(frozen=True, slots=True)
class HistorySource:
    type: HistoryKind
    id: uuid.UUID
    report_id: uuid.UUID | None = None
    report_source_name: str | None = None


@dataclass(frozen=True, slots=True)
class HistoryItem:
    id: uuid.UUID
    kind: HistoryKind
    occurred_at: datetime
    title: str
    primary_value: str | None
    unit: str | None
    detail: str | None
    source: HistorySource
    created_at: datetime


def _format_number(value: object) -> str:
    formatted = format(value, "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def _metric_value(metric: HealthMetric) -> str:
    values: list[str] = []
    if metric.systolic_bp_mm_hg is not None and metric.diastolic_bp_mm_hg is not None:
        values.append(f"{metric.systolic_bp_mm_hg}/{metric.diastolic_bp_mm_hg} mmHg")
    if metric.heart_rate_bpm is not None:
        values.append(f"{metric.heart_rate_bpm} bpm")
    if metric.weight_kg is not None:
        values.append(f"{_format_number(metric.weight_kg)} kg")
    if metric.blood_glucose_mg_dl is not None:
        values.append(f"{_format_number(metric.blood_glucose_mg_dl)} mg/dL")
    return " · ".join(values)


def _symptom_detail(symptom: SymptomLog) -> str:
    details = [f"Severity {symptom.severity}/5"]
    if symptom.duration_minutes is not None:
        details.append(f"{symptom.duration_minutes} minutes")
    if symptom.note:
        details.append(symptom.note)
    return " · ".join(details)


def _report_observation_value(observation: HealthReportObservationModel) -> str | None:
    if observation.value_numeric is not None:
        return _format_number(observation.value_numeric)
    return observation.value_text


def build_history(
    metrics: list[HealthMetric],
    symptoms: list[SymptomLog],
    report_observations: list[HealthReportObservationModel],
) -> list[HistoryItem]:
    items = [
        *[
            HistoryItem(
                id=metric.id,
                kind="metric",
                occurred_at=metric.recorded_at,
                title="Health metric",
                primary_value=_metric_value(metric),
                unit=None,
                detail=metric.note,
                source=HistorySource(type="metric", id=metric.id),
                created_at=metric.created_at,
            )
            for metric in metrics
        ],
        *[
            HistoryItem(
                id=symptom.id,
                kind="symptom",
                occurred_at=symptom.occurred_at,
                title="Symptom",
                primary_value=symptom.symptom,
                unit=None,
                detail=_symptom_detail(symptom),
                source=HistorySource(type="symptom", id=symptom.id),
                created_at=symptom.created_at,
            )
            for symptom in symptoms
        ],
        *[
            HistoryItem(
                id=observation.id,
                kind="report_observation",
                occurred_at=observation.observed_at,
                title=observation.display_name,
                primary_value=_report_observation_value(observation),
                unit=observation.unit,
                detail=(
                    f"Reference: {observation.reference_range}"
                    if observation.reference_range
                    else None
                ),
                source=HistorySource(
                    type="report_observation",
                    id=observation.id,
                    report_id=observation.report_id,
                    report_source_name=observation.report.source_name,
                ),
                created_at=observation.created_at,
            )
            for observation in report_observations
        ],
    ]
    items.sort(
        key=lambda item: (item.occurred_at, item.created_at, item.kind, str(item.id)),
        reverse=True,
    )
    return items
