from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from healthy.application.health_score_inputs import LEGACY_LAB_ALIASES, LEGACY_LAB_UNITS
from healthy.infrastructure.models import HealthMetric, HealthReportObservationModel

RiskAlertSeverity = Literal["medium", "high"]
RiskAlertStatus = Literal["active"]
RiskAlertSourceKind = Literal["metric", "report_observation"]


@dataclass(frozen=True, slots=True)
class RiskAlertEvidence:
    source_kind: RiskAlertSourceKind
    source_id: uuid.UUID
    person_id: uuid.UUID
    observed_at: datetime
    report_id: uuid.UUID | None = None
    report_source_name: str | None = None


@dataclass(frozen=True, slots=True)
class RiskAlertInput:
    rule_code: str
    risk_type: str
    severity: RiskAlertSeverity
    evidence: RiskAlertEvidence
    status: RiskAlertStatus = "active"


@dataclass(frozen=True, slots=True)
class RiskAlertsInput:
    alerts: tuple[RiskAlertInput, ...]

    @property
    def active_count(self) -> int:
        return len(self.alerts)


@dataclass(frozen=True, slots=True)
class _LabRule:
    rule_code: str
    label: str
    operator: Literal["gte", "lt"]
    threshold: Decimal
    severity: RiskAlertSeverity


_BMI_UNDERWEIGHT = Decimal("18.5")
_BMI_OVERWEIGHT = Decimal("24.0")
_BMI_OBESE = Decimal("27.0")
_BP_SYSTOLIC_HIGH = Decimal("130")
_BP_DIASTOLIC_HIGH = Decimal("80")
_GLUCOSE_HIGH = Decimal("126")

_LAB_ALIASES: dict[str, str] = {
    alias.casefold(): canonical
    for canonical, aliases in (
        *LEGACY_LAB_ALIASES.items(),
        ("AST", ("AST",)),
        ("Uric Acid", ("Uric Acid", "UricAcid")),
    )
    for alias in aliases
}
_LAB_UNITS: dict[str, str] = {
    **{str(key): unit for key, unit in LEGACY_LAB_UNITS.items()},
    "AST": "U/L",
    "Uric Acid": "mg/dL",
}
_LAB_RULES: tuple[_LabRule, ...] = (
    _LabRule("LIVER_ALT_HIGH", "ALT", "gte", Decimal("40"), "medium"),
    _LabRule("LIVER_AST_HIGH", "AST", "gte", Decimal("40"), "medium"),
    _LabRule("UA_HIGH", "Uric Acid", "gte", Decimal("7.0"), "medium"),
    _LabRule(
        "LIPID_CHOLESTEROL_HIGH",
        "Total Cholesterol",
        "gte",
        Decimal("200"),
        "medium",
    ),
    _LabRule("LIPID_LDL_HIGH", "LDL", "gte", Decimal("130"), "medium"),
    _LabRule("LIPID_HDL_LOW", "HDL", "lt", Decimal("40"), "medium"),
    _LabRule("LIPID_TG_HIGH", "Triglycerides", "gte", Decimal("150"), "medium"),
)


def build_risk_alerts_input(
    metrics: Iterable[HealthMetric],
    observations: Iterable[HealthReportObservationModel],
    *,
    person_id: uuid.UUID,
    height_cm: Decimal | None,
) -> RiskAlertsInput:
    """Derive the safe, deterministic legacy metric/lab alert subset.

    The legacy persisted producer evaluated each metric or lab observation as
    it arrived, and the legacy Health Score counted active alerts without an
    age predicate.  This read model therefore evaluates all person-owned
    records, emits one immutable alert per matching source record and rule,
    and retains source/report provenance without writing derived state.

    The legacy monitor rules that depend on AI summaries, external metrics or
    long-term symptom duration are intentionally outside this evaluator.
    """
    alerts = [
        *_build_metric_alerts(metrics, person_id=person_id, height_cm=height_cm),
        *_build_lab_alerts(observations, person_id=person_id),
    ]
    return RiskAlertsInput(alerts=tuple(alerts))


def _build_metric_alerts(
    metrics: Iterable[HealthMetric],
    *,
    person_id: uuid.UUID,
    height_cm: Decimal | None,
) -> list[RiskAlertInput]:
    alerts: list[RiskAlertInput] = []
    for metric in sorted(metrics, key=_metric_sort_key, reverse=True):
        if metric.person_id != person_id:
            continue
        observed_at = _as_utc(metric.recorded_at)
        if observed_at is None:
            continue

        bmi = _calculate_bmi(metric.weight_kg, height_cm)
        if bmi is not None:
            if bmi < _BMI_UNDERWEIGHT:
                alerts.append(_metric_alert("BMI_UNDER", "medium", metric, person_id, observed_at))
            elif bmi >= _BMI_OBESE:
                alerts.append(_metric_alert("BMI_OBESE", "high", metric, person_id, observed_at))
            elif bmi >= _BMI_OVERWEIGHT:
                alerts.append(_metric_alert("BMI_OVER", "medium", metric, person_id, observed_at))

        systolic = _as_decimal(metric.systolic_bp_mm_hg)
        diastolic = _as_decimal(metric.diastolic_bp_mm_hg)
        if (
            systolic is not None
            and systolic >= _BP_SYSTOLIC_HIGH
            or diastolic is not None
            and diastolic >= _BP_DIASTOLIC_HIGH
        ):
            alerts.append(_metric_alert("BP_HIGH", "high", metric, person_id, observed_at))

        glucose = _as_decimal(metric.blood_glucose_mg_dl)
        if glucose is not None and glucose >= _GLUCOSE_HIGH:
            alerts.append(_metric_alert("GLUCOSE_HIGH", "high", metric, person_id, observed_at))
    return alerts


def _build_lab_alerts(
    observations: Iterable[HealthReportObservationModel],
    *,
    person_id: uuid.UUID,
) -> list[RiskAlertInput]:
    alerts: list[RiskAlertInput] = []
    for observation in sorted(observations, key=_observation_sort_key, reverse=True):
        if observation.person_id != person_id:
            continue
        report = observation.report
        if report.person_id != person_id or report.status != "confirmed":
            continue
        observed_at = _as_utc(observation.observed_at)
        value = _as_decimal(observation.value_numeric)
        if observed_at is None or value is None:
            continue

        label = _LAB_ALIASES.get(observation.display_name.strip().casefold())
        if label is None:
            continue
        unit = _normalize_unit(observation.unit)
        if unit is not None and unit != _LAB_UNITS[label]:
            continue

        for rule in _LAB_RULES:
            if rule.label != label or not _matches(rule, value):
                continue
            alerts.append(
                RiskAlertInput(
                    rule_code=rule.rule_code,
                    risk_type=rule.rule_code.lower(),
                    severity=rule.severity,
                    evidence=RiskAlertEvidence(
                        source_kind="report_observation",
                        source_id=observation.id,
                        person_id=person_id,
                        observed_at=observed_at,
                        report_id=observation.report_id,
                        report_source_name=report.source_name,
                    ),
                )
            )
    return alerts


def _metric_alert(
    rule_code: str,
    severity: RiskAlertSeverity,
    metric: HealthMetric,
    person_id: uuid.UUID,
    observed_at: datetime,
) -> RiskAlertInput:
    return RiskAlertInput(
        rule_code=rule_code,
        risk_type=rule_code.lower(),
        severity=severity,
        evidence=RiskAlertEvidence(
            source_kind="metric",
            source_id=metric.id,
            person_id=person_id,
            observed_at=observed_at,
        ),
    )


def _matches(rule: _LabRule, value: Decimal) -> bool:
    if rule.operator == "gte":
        return value >= rule.threshold
    return value < rule.threshold


def _calculate_bmi(weight_kg: Decimal | None, height_cm: Decimal | None) -> Decimal | None:
    weight = _as_decimal(weight_kg)
    height = _as_decimal(height_cm)
    if weight is None or height is None or height <= 0:
        return None
    height_m = height / Decimal("100")
    return weight / (height_m * height_m)


def _metric_sort_key(metric: HealthMetric) -> tuple[datetime, datetime, str]:
    return (
        _sort_timestamp(metric.recorded_at),
        _sort_timestamp(metric.created_at),
        str(metric.id),
    )


def _observation_sort_key(
    observation: HealthReportObservationModel,
) -> tuple[datetime, datetime, str]:
    return (
        _sort_timestamp(observation.observed_at),
        _sort_timestamp(observation.created_at),
        str(observation.id),
    )


def _sort_timestamp(value: datetime | None) -> datetime:
    return _as_utc(value) or datetime.min.replace(tzinfo=UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation, ValueError:
        return None


def _normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    normalized = unit.strip()
    if not normalized:
        return None
    if normalized.casefold() == "iu/l":
        return "U/L"
    return normalized
