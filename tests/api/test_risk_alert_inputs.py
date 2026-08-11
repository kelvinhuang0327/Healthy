from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from healthy.application import services
from healthy.application.risk_alert_inputs import build_risk_alerts_input
from healthy.infrastructure.models import (
    HealthMetric,
    HealthReportModel,
    HealthReportObservationModel,
    Person,
)

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _uuid(number: int) -> uuid.UUID:
    return uuid.UUID(int=number)


def _metric(
    person_id: uuid.UUID,
    *,
    number: int,
    recorded_at: datetime = NOW,
    weight_kg: Decimal | None = None,
    systolic_bp_mm_hg: int | None = None,
    diastolic_bp_mm_hg: int | None = None,
    blood_glucose_mg_dl: Decimal | None = None,
) -> HealthMetric:
    return HealthMetric(
        id=_uuid(number),
        person_id=person_id,
        recorded_at=recorded_at,
        created_at=recorded_at,
        weight_kg=weight_kg,
        systolic_bp_mm_hg=systolic_bp_mm_hg,
        diastolic_bp_mm_hg=diastolic_bp_mm_hg,
        blood_glucose_mg_dl=blood_glucose_mg_dl,
    )


def _observation(
    person_id: uuid.UUID,
    *,
    number: int,
    label: str,
    value: Decimal | None,
    unit: str | None,
    status: str = "confirmed",
    observed_at: datetime = NOW,
) -> HealthReportObservationModel:
    report = HealthReportModel(
        id=_uuid(10_000 + number),
        person_id=person_id,
        schema_version="healthy.health-report.v1",
        source_name=f"Report {number}",
        reported_at=observed_at,
        canonical_sha256=f"{number:064x}",
        status=status,
        created_at=observed_at,
    )
    return HealthReportObservationModel(
        id=_uuid(number),
        report_id=report.id,
        person_id=person_id,
        code=label.upper(),
        display_name=label,
        value_numeric=value,
        value_text=None,
        unit=unit,
        reference_range=None,
        observed_at=observed_at,
        created_at=observed_at,
        report=report,
    )


def test_metric_rules_match_legacy_codes_and_are_deterministic() -> None:
    person_id = _uuid(1)
    metric = _metric(
        person_id,
        number=1,
        weight_kg=Decimal("90"),
        systolic_bp_mm_hg=145,
        diastolic_bp_mm_hg=95,
        blood_glucose_mg_dl=Decimal("140"),
    )

    forward = build_risk_alerts_input(
        [metric],
        [],
        person_id=person_id,
        height_cm=Decimal("170"),
    )
    reverse = build_risk_alerts_input(
        list(reversed([metric])),
        [],
        person_id=person_id,
        height_cm=Decimal("170"),
    )

    assert forward == reverse
    assert [alert.rule_code for alert in forward.alerts] == [
        "BMI_OBESE",
        "BP_HIGH",
        "GLUCOSE_HIGH",
    ]
    assert {alert.severity for alert in forward.alerts} == {"high"}
    assert all(alert.status == "active" for alert in forward.alerts)
    assert all(alert.evidence.source_id == metric.id for alert in forward.alerts)


def test_lab_rules_preserve_evidence_and_reject_incompatible_units() -> None:
    person_id = _uuid(2)
    observations = [
        _observation(
            person_id,
            number=1,
            label="ALT",
            value=Decimal("50"),
            unit="IU/L",
        ),
        _observation(
            person_id,
            number=2,
            label="HDL",
            value=Decimal("35"),
            unit="mg/dL",
        ),
        _observation(
            person_id,
            number=3,
            label="LDL",
            value=Decimal("140"),
            unit="mmol/L",
        ),
    ]

    result = build_risk_alerts_input([], observations, person_id=person_id, height_cm=None)

    assert [alert.rule_code for alert in result.alerts] == [
        "LIPID_HDL_LOW",
        "LIVER_ALT_HIGH",
    ]
    alt_alert = result.alerts[1]
    assert alt_alert.evidence.source_kind == "lab_report"
    assert alt_alert.evidence.source_id == observations[0].report_id
    assert alt_alert.evidence.observation_id == observations[0].id
    assert alt_alert.evidence.person_id == person_id
    assert alt_alert.evidence.report_id == observations[0].report_id
    assert alt_alert.evidence.report_source_name == "Report 1"
    assert alt_alert.evidence.observed_at == observations[0].observed_at


def test_legacy_parser_aliases_are_supported_without_fuzzy_synonyms() -> None:
    person_id = _uuid(11)
    observations = [
        _observation(
            person_id,
            number=1,
            label="GOT (AST)",
            value=Decimal("50"),
            unit="U/L",
        ),
        _observation(
            person_id,
            number=2,
            label="尿酸",
            value=Decimal("8"),
            unit="mg/dL",
        ),
        _observation(
            person_id,
            number=3,
            label="UricAcid",
            value=Decimal("8"),
            unit="mg/dL",
        ),
    ]

    result = build_risk_alerts_input([], observations, person_id=person_id, height_cm=None)

    assert [alert.rule_code for alert in result.alerts] == ["UA_HIGH", "LIVER_AST_HIGH"]
    assert result.alerts[0].evidence.observation_id == observations[1].id
    assert result.alerts[1].evidence.observation_id == observations[0].id


def test_missing_or_non_finite_lab_values_preserve_absence() -> None:
    person_id = _uuid(12)
    observations = [
        _observation(
            person_id,
            number=1,
            label="HDL",
            value=None,
            unit="mg/dL",
        ),
        _observation(
            person_id,
            number=2,
            label="HDL",
            value=Decimal("NaN"),
            unit="mg/dL",
        ),
    ]

    result = build_risk_alerts_input([], observations, person_id=person_id, height_cm=None)

    assert result.alerts == ()


def test_person_scope_missing_inputs_and_pending_reports_do_not_emit_alerts() -> None:
    person_id = _uuid(3)
    foreign_id = _uuid(4)
    foreign_metric = _metric(
        foreign_id,
        number=1,
        weight_kg=Decimal("90"),
        systolic_bp_mm_hg=150,
        diastolic_bp_mm_hg=95,
        blood_glucose_mg_dl=Decimal("140"),
    )
    pending = _observation(
        person_id,
        number=2,
        label="ALT",
        value=Decimal("50"),
        unit="U/L",
        status="pending",
    )
    foreign_observation = _observation(
        foreign_id,
        number=3,
        label="ALT",
        value=Decimal("50"),
        unit="U/L",
    )

    result = build_risk_alerts_input(
        [foreign_metric],
        [pending, foreign_observation],
        person_id=person_id,
        height_cm=None,
    )

    assert result.alerts == ()


def test_legacy_persisted_alerts_have_no_implicit_age_window() -> None:
    person_id = _uuid(5)
    old_metric = _metric(
        person_id,
        number=1,
        recorded_at=NOW - timedelta(days=365),
        systolic_bp_mm_hg=150,
        diastolic_bp_mm_hg=95,
    )

    result = build_risk_alerts_input([old_metric], [], person_id=person_id, height_cm=None)

    assert [alert.rule_code for alert in result.alerts] == ["BP_HIGH"]


def test_unsupported_ai_and_symptom_alerts_are_not_fabricated() -> None:
    person_id = _uuid(6)
    metric = _metric(
        person_id,
        number=1,
        systolic_bp_mm_hg=150,
        diastolic_bp_mm_hg=95,
    )

    result = build_risk_alerts_input([metric], [], person_id=person_id, height_cm=None)

    codes = {alert.rule_code for alert in result.alerts}
    assert codes == {"BP_HIGH"}
    assert "BP_HIGH_3TIMES" not in codes
    assert "LONG_TERM_SYMPTOM" not in codes
    assert "AI_SUMMARY_HIGH_RISK" not in codes
    assert "EXTERNAL_METRICS_ACTIVE" not in codes


def test_application_service_wires_person_sources_without_writes() -> None:
    owner_id = _uuid(7)
    person_id = _uuid(8)
    person = Person(
        id=person_id,
        owner_account_id=owner_id,
        display_name="Owner",
        relationship="self",
        height_cm=Decimal("170"),
        is_default=True,
    )
    metric = _metric(
        person_id,
        number=9,
        weight_kg=Decimal("90"),
        systolic_bp_mm_hg=145,
        diastolic_bp_mm_hg=95,
    )
    observation = _observation(
        person_id,
        number=10,
        label="ALT",
        value=Decimal("50"),
        unit="U/L",
    )
    session = Mock()

    with patch.object(
        services.PersonRepository,
        "get_for_owner",
        return_value=person,
    ) as get_person:
        with patch.object(
            services.HealthMetricRepository,
            "list_for_person",
            return_value=[metric],
        ) as list_metrics:
            with patch.object(
                services.SymptomLogRepository,
                "list_for_person",
                return_value=[],
            ) as list_symptoms:
                with patch.object(
                    services.HealthReportRepository,
                    "list_confirmed_observations_for_person",
                    return_value=[observation],
                ) as list_observations:
                    result = services.get_health_score_inputs(
                        session,
                        owner_account_id=owner_id,
                        person_id=person_id,
                        now=NOW,
                    )

    get_person.assert_called_once_with(session, owner_id, person_id)
    list_metrics.assert_called_once_with(session, person_id)
    list_symptoms.assert_called_once_with(session, person_id)
    list_observations.assert_called_once_with(session, person_id)
    session.add.assert_not_called()
    session.commit.assert_not_called()
    session.flush.assert_not_called()
    assert result is not None
    assert [alert.rule_code for alert in result.risk_alerts.alerts] == [
        "BMI_OBESE",
        "BP_HIGH",
        "LIVER_ALT_HIGH",
    ]
    assert all(alert.evidence.person_id == person_id for alert in result.risk_alerts.alerts)
    assert result.risk_alerts.alerts[1].evidence.source_kind == "health_metric"
    assert result.risk_alerts.alerts[2].evidence.source_kind == "lab_report"
