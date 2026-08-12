from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

ActionRecommendationSeverity = Literal["medium", "high"]
ActionRecommendationSourceKind = Literal["health_metric", "lab_report"]

RULE_VERSION = "risk-action-recommendations-v1"

_SUGGESTED_ACTION = (
    "Review the source record and its context. If you need help understanding "
    "what this signal means for you, consider discussing it with a qualified "
    "healthcare professional."
)
_LIMITATIONS = (
    "This is a deterministic follow-up suggestion based on recorded data, not a "
    "diagnosis, treatment plan, or clinical urgency assessment."
)
_RULE_LABELS = {
    "BMI_UNDER": "BMI signal",
    "BMI_OVER": "BMI signal",
    "BMI_OBESE": "BMI signal",
    "BP_HIGH": "Blood pressure signal",
    "GLUCOSE_HIGH": "Blood glucose signal",
    "LIVER_ALT_HIGH": "ALT lab signal",
    "LIVER_AST_HIGH": "AST lab signal",
    "UA_HIGH": "Uric acid lab signal",
    "LIPID_CHOLESTEROL_HIGH": "Total cholesterol signal",
    "LIPID_LDL_HIGH": "LDL signal",
    "LIPID_HDL_LOW": "HDL signal",
    "LIPID_TG_HIGH": "Triglyceride signal",
}


class _RiskAlertEvidenceLike(Protocol):
    @property
    def source_kind(self) -> ActionRecommendationSourceKind: ...

    @property
    def source_id(self) -> uuid.UUID: ...

    @property
    def person_id(self) -> uuid.UUID: ...

    @property
    def observed_at(self) -> datetime: ...

    @property
    def observation_id(self) -> uuid.UUID | None: ...

    @property
    def report_id(self) -> uuid.UUID | None: ...

    @property
    def report_source_name(self) -> str | None: ...


class _RiskAlertLike(Protocol):
    @property
    def rule_code(self) -> str: ...

    @property
    def risk_type(self) -> str: ...

    @property
    def severity(self) -> ActionRecommendationSeverity: ...

    @property
    def status(self) -> Literal["active"]: ...

    @property
    def evidence(self) -> _RiskAlertEvidenceLike: ...


@dataclass(frozen=True, slots=True)
class ActionRecommendationEvidence:
    source_kind: ActionRecommendationSourceKind
    source_id: uuid.UUID
    person_id: uuid.UUID
    observed_at: datetime
    observation_id: uuid.UUID | None = None
    report_id: uuid.UUID | None = None
    report_source_name: str | None = None


@dataclass(frozen=True, slots=True)
class ActionRecommendation:
    recommendation_code: str
    source_rule_code: str
    source_risk_type: str
    source_severity: ActionRecommendationSeverity
    title: str
    rationale: str
    suggested_action: str
    matching_alert_count: int
    rule_version: str
    limitations: str
    evidence: ActionRecommendationEvidence


@dataclass(frozen=True, slots=True)
class ActionRecommendations:
    recommendations: tuple[ActionRecommendation, ...]


def recommendation_identity_fingerprint(
    *,
    person_id: uuid.UUID,
    recommendation_code: str,
    rule_version: str,
    source_kind: ActionRecommendationSourceKind,
    source_id: uuid.UUID,
    observation_id: uuid.UUID | None,
    report_id: uuid.UUID | None,
) -> str:
    """Return the stable idempotency identity for one exact recommendation."""
    canonical_identity = json.dumps(
        {
            "observation_id": str(observation_id) if observation_id is not None else None,
            "person_id": str(person_id),
            "recommendation_code": recommendation_code,
            "report_id": str(report_id) if report_id is not None else None,
            "rule_version": rule_version,
            "source_id": str(source_id),
            "source_kind": source_kind,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()


def build_action_recommendations(
    risk_alerts: tuple[_RiskAlertLike, ...] | list[_RiskAlertLike],
) -> ActionRecommendations:
    """Build a deterministic, evidence-linked recommendation read model."""
    alerts_by_rule: dict[str, list[_RiskAlertLike]] = {}
    for alert in risk_alerts:
        if alert.status == "active":
            alerts_by_rule.setdefault(alert.rule_code, []).append(alert)

    recommendations = [_build_recommendation(alerts) for alerts in alerts_by_rule.values()]
    recommendations.sort(key=_recommendation_sort_key)
    return ActionRecommendations(recommendations=tuple(recommendations))


def _build_recommendation(alerts: list[_RiskAlertLike]) -> ActionRecommendation:
    primary_alert = max(alerts, key=_primary_alert_key)
    evidence = primary_alert.evidence
    evidence_projection = ActionRecommendationEvidence(
        source_kind=evidence.source_kind,
        source_id=evidence.source_id,
        person_id=evidence.person_id,
        observed_at=evidence.observed_at,
        observation_id=evidence.observation_id,
        report_id=evidence.report_id,
        report_source_name=evidence.report_source_name,
    )
    rule_code = primary_alert.rule_code
    evidence_label = (
        "recorded health metric"
        if evidence.source_kind == "health_metric"
        else "imported lab report"
    )
    title = _RULE_LABELS.get(rule_code, "Review this health signal")
    return ActionRecommendation(
        recommendation_code=f"REVIEW_{rule_code}",
        source_rule_code=rule_code,
        source_risk_type=primary_alert.risk_type,
        source_severity=primary_alert.severity,
        title=title,
        rationale=(
            f"The deterministic {rule_code} rule produced this signal from "
            f"{evidence_label} evidence ({evidence.source_id}); it reflects "
            "recorded/imported data."
        ),
        suggested_action=_SUGGESTED_ACTION,
        matching_alert_count=len(alerts),
        rule_version=RULE_VERSION,
        limitations=_LIMITATIONS,
        evidence=evidence_projection,
    )


def _primary_alert_key(alert: _RiskAlertLike) -> tuple[datetime, str, str, str]:
    evidence = alert.evidence
    return (
        _as_utc(evidence.observed_at),
        str(evidence.source_id),
        str(evidence.observation_id or ""),
        str(evidence.report_id or ""),
    )


def _recommendation_sort_key(
    recommendation: ActionRecommendation,
) -> tuple[int, float, str, str]:
    severity_rank = {"high": 0, "medium": 1}[recommendation.source_severity]
    return (
        severity_rank,
        -_as_utc(recommendation.evidence.observed_at).timestamp(),
        recommendation.source_rule_code,
        str(recommendation.evidence.source_id),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
