from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from healthy.application.symptom_duration import (
    SymptomDurationInput,
    build_symptom_duration_input,
)
from healthy.infrastructure.models import HealthMetric, HealthReportObservationModel, SymptomLog

if TYPE_CHECKING:
    from healthy.application.risk_alert_inputs import RiskAlertsInput

LEGACY_LAB_LOOKBACK_DAYS = 30
LegacyLabKey = Literal[
    "Total Cholesterol",
    "LDL",
    "HDL",
    "Triglycerides",
    "ALT",
]

LEGACY_LAB_KEYS: tuple[LegacyLabKey, ...] = (
    "Total Cholesterol",
    "LDL",
    "HDL",
    "Triglycerides",
    "ALT",
)

# These are the exact canonical labels and aliases present in the legacy
# report parser for the five keys consumed by the legacy score.  Matching is
# exact after trimming and case-folding; substring/fuzzy matching is
# intentionally not portable into Healthy.
LEGACY_LAB_ALIASES: Mapping[LegacyLabKey, tuple[str, ...]] = {
    "Total Cholesterol": ("Total Cholesterol", "膽固醇"),
    "LDL": ("LDL",),
    "HDL": ("HDL",),
    "Triglycerides": ("Triglycerides", "三酸甘油脂"),
    "ALT": ("ALT", "GPT"),
}

LEGACY_LAB_UNITS: Mapping[LegacyLabKey, str] = {
    "Total Cholesterol": "mg/dL",
    "LDL": "mg/dL",
    "HDL": "mg/dL",
    "Triglycerides": "mg/dL",
    "ALT": "U/L",
}

_LABEL_TO_LEGACY_KEY: dict[str, LegacyLabKey] = {
    alias.casefold(): key for key, aliases in LEGACY_LAB_ALIASES.items() for alias in aliases
}


@dataclass(frozen=True, slots=True)
class HealthScoreEvidence:
    source_kind: Literal["report_observation"]
    source_id: uuid.UUID
    report_id: uuid.UUID
    person_id: uuid.UUID
    observed_at: datetime
    report_source_name: str


@dataclass(frozen=True, slots=True)
class NamedLabValue:
    key: LegacyLabKey
    value: Decimal
    unit: str | None
    evidence: HealthScoreEvidence


@dataclass(frozen=True, slots=True)
class NamedLabsInput:
    values: Mapping[LegacyLabKey, NamedLabValue | None]
    missing_keys: tuple[LegacyLabKey, ...]

    def value_for(self, key: LegacyLabKey) -> NamedLabValue | None:
        return self.values[key]


@dataclass(frozen=True, slots=True)
class HealthScoreInputs:
    named_labs: NamedLabsInput
    risk_alerts: RiskAlertsInput
    symptom_duration: SymptomDurationInput


def canonical_legacy_lab_key(label: str) -> LegacyLabKey | None:
    """Return a legacy score key only for an exact supported label/alias."""
    return _LABEL_TO_LEGACY_KEY.get(label.strip().casefold())


def normalize_supported_lab_unit(unit: str | None) -> str | None:
    """Normalize only the unit spelling explicitly supported by the legacy parser."""
    if unit is None:
        return None
    normalized = unit.strip()
    if not normalized:
        return None
    if normalized.casefold() == "iu/l":
        return "U/L"
    return normalized


def build_named_labs_input(
    observations: Iterable[HealthReportObservationModel],
    *,
    person_id: uuid.UUID,
    window_start: datetime | None = None,
) -> NamedLabsInput:
    """Map confirmed Healthy report observations to legacy named lab inputs.

    The legacy score selected numeric values from reports created within its
    lookback window and chose the newest captured value per canonical key.  A
    missing unit is retained because the legacy unit guard treats it as
    unknown, while a present incompatible unit is left missing.  No numeric
    unit conversion is performed.
    """
    normalized_window_start = _as_utc(window_start) if window_start is not None else None
    selected: dict[LegacyLabKey, HealthReportObservationModel] = {}

    for observation in observations:
        if observation.person_id != person_id:
            continue
        report = observation.report
        if report.person_id != person_id or report.status != "confirmed":
            continue

        key = canonical_legacy_lab_key(observation.display_name)
        if key is None or observation.value_numeric is None:
            continue

        report_created_at = _as_utc(report.created_at)
        observed_at = _as_utc(observation.observed_at)
        created_at = _as_utc(observation.created_at)
        if report_created_at is None or observed_at is None or created_at is None:
            continue
        if normalized_window_start is not None and report_created_at < normalized_window_start:
            continue

        normalized_unit = normalize_supported_lab_unit(observation.unit)
        if normalized_unit is not None and normalized_unit != LEGACY_LAB_UNITS[key]:
            continue

        current = selected.get(key)
        if current is None or _selection_key(observation) > _selection_key(current):
            selected[key] = observation

    values: dict[LegacyLabKey, NamedLabValue | None] = {key: None for key in LEGACY_LAB_KEYS}
    for key, observation in selected.items():
        observed_at = _as_utc(observation.observed_at)
        if observed_at is None:
            raise ValueError("selected observations must have timezone-aware timestamps")
        values[key] = NamedLabValue(
            key=key,
            value=Decimal(str(observation.value_numeric)),
            unit=normalize_supported_lab_unit(observation.unit),
            evidence=HealthScoreEvidence(
                source_kind="report_observation",
                source_id=observation.id,
                report_id=observation.report_id,
                person_id=observation.person_id,
                observed_at=observed_at,
                report_source_name=observation.report.source_name,
            ),
        )

    return NamedLabsInput(
        values=MappingProxyType(values),
        missing_keys=tuple(key for key in LEGACY_LAB_KEYS if values[key] is None),
    )


def build_health_score_inputs(
    observations: Iterable[HealthReportObservationModel],
    *,
    person_id: uuid.UUID,
    now: datetime,
    lookback_days: int = LEGACY_LAB_LOOKBACK_DAYS,
    metrics: Iterable[HealthMetric] = (),
    symptoms: Iterable[SymptomLog] = (),
    height_cm: Decimal | None = None,
) -> HealthScoreInputs:
    """Build the reusable score-input read model without persisting derived data."""
    from healthy.application.risk_alert_inputs import build_risk_alerts_input

    normalized_now = _as_utc(now)
    if normalized_now is None:
        raise ValueError("now must be timezone-aware")
    if lookback_days < 0:
        raise ValueError("lookback_days must not be negative")

    observation_list = tuple(observations)
    metric_list = tuple(metrics)
    symptom_list = tuple(symptoms)

    return HealthScoreInputs(
        named_labs=build_named_labs_input(
            observation_list,
            person_id=person_id,
            window_start=normalized_now - timedelta(days=lookback_days),
        ),
        risk_alerts=build_risk_alerts_input(
            metric_list,
            observation_list,
            person_id=person_id,
            height_cm=height_cm,
        ),
        symptom_duration=build_symptom_duration_input(
            symptom_list,
            person_id=person_id,
        ),
    )


def _selection_key(observation: HealthReportObservationModel) -> tuple[datetime, datetime, str]:
    observed_at = _as_utc(observation.observed_at)
    created_at = _as_utc(observation.created_at)
    if observed_at is None or created_at is None:
        raise ValueError("selected observations must have timezone-aware timestamps")
    return observed_at, created_at, str(observation.id)


def _as_utc(value: datetime) -> datetime | None:
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)
