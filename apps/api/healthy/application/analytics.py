from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from healthy.infrastructure.models import HealthMetric

AnalyticsDirection = Literal["up", "down", "stable", "no_data"]


@dataclass(frozen=True, slots=True)
class AnalyticsSummary:
    metric: str
    label: str
    unit: str
    points: int
    first_value: float | None
    last_value: float | None
    change_percent: float | None
    slope_per_day: float | None
    direction: AnalyticsDirection


@dataclass(frozen=True, slots=True)
class HealthAnalytics:
    period_days: int
    summaries: tuple[AnalyticsSummary, ...]


_TREND_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("systolic_bp_mm_hg", "Systolic blood pressure", "mmHg", "systolic_bp_mm_hg"),
    ("diastolic_bp_mm_hg", "Diastolic blood pressure", "mmHg", "diastolic_bp_mm_hg"),
    ("heart_rate_bpm", "Heart rate", "bpm", "heart_rate_bpm"),
    ("steps", "Steps", "steps", "steps"),
    ("weight_kg", "Weight", "kg", "weight_kg"),
    ("blood_glucose_mg_dl", "Blood glucose", "mg/dL", "blood_glucose_mg_dl"),
    ("sleep_hours", "Sleep", "hours", "sleep_hours"),
)


def _summarize_series(
    metric: str,
    label: str,
    unit: str,
    series: list[tuple[datetime, float]],
) -> AnalyticsSummary:
    if not series:
        return AnalyticsSummary(
            metric=metric,
            label=label,
            unit=unit,
            points=0,
            first_value=None,
            last_value=None,
            change_percent=None,
            slope_per_day=None,
            direction="no_data",
        )

    first_time, first_value = series[0]
    last_time, last_value = series[-1]
    change_percent = None
    if abs(first_value) > 1e-9:
        change_percent = ((last_value - first_value) / abs(first_value)) * 100

    day_span = max((last_time - first_time).total_seconds() / 86400, 1e-9)
    slope_per_day = (last_value - first_value) / day_span if len(series) > 1 else 0.0

    direction: AnalyticsDirection = "stable"
    if change_percent is not None:
        if change_percent > 2:
            direction = "up"
        elif change_percent < -2:
            direction = "down"

    return AnalyticsSummary(
        metric=metric,
        label=label,
        unit=unit,
        points=len(series),
        first_value=round(first_value, 3),
        last_value=round(last_value, 3),
        change_percent=round(change_percent, 3) if change_percent is not None else None,
        slope_per_day=round(slope_per_day, 4),
        direction=direction,
    )


def build_health_analytics(
    metrics: Sequence[HealthMetric],
    *,
    period_days: int,
) -> HealthAnalytics:
    ordered_metrics = sorted(
        metrics,
        key=lambda metric: (metric.recorded_at, metric.created_at, str(metric.id)),
    )
    summaries = []
    for metric_name, label, unit, field_name in _TREND_FIELDS:
        series = []
        for metric in ordered_metrics:
            value = getattr(metric, field_name)
            if value is not None:
                series.append((metric.recorded_at, float(value)))
        summaries.append(_summarize_series(metric_name, label, unit, series))

    return HealthAnalytics(period_days=period_days, summaries=tuple(summaries))
