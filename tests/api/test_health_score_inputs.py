from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import DATABASE_URL, ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.application import services
from healthy.application.health_score_inputs import (
    LEGACY_LAB_KEYS,
    build_health_score_inputs,
    build_named_labs_input,
    canonical_legacy_lab_key,
)
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import (
    HealthReportModel,
    HealthReportObservationModel,
    Person,
    SymptomLog,
)
from sqlalchemy import select

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _uuid(number: int) -> uuid.UUID:
    return uuid.UUID(int=number)


def _observation(
    *,
    person_id: uuid.UUID,
    label: str,
    value: Decimal | None,
    unit: str | None,
    observed_at: datetime = NOW - timedelta(days=1),
    report_created_at: datetime | None = None,
    created_at: datetime | None = None,
    status: str = "confirmed",
    number: int = 1,
) -> HealthReportObservationModel:
    report_id = _uuid(10_000 + number)
    report = HealthReportModel(
        id=report_id,
        person_id=person_id,
        schema_version="healthy.health-report.v1",
        source_name=f"Report {number}",
        reported_at=observed_at,
        canonical_sha256=f"{number:064x}",
        status=status,
        created_at=report_created_at or observed_at,
    )
    return HealthReportObservationModel(
        id=_uuid(number),
        report_id=report_id,
        person_id=person_id,
        code=label.upper(),
        display_name=label,
        value_numeric=value,
        value_text=None,
        unit=unit,
        reference_range=None,
        observed_at=observed_at,
        created_at=created_at or observed_at,
        report=report,
    )


def test_exact_legacy_labels_and_aliases_map_to_named_inputs() -> None:
    person_id = _uuid(1)
    observations = [
        _observation(
            person_id=person_id,
            label="膽固醇",
            value=Decimal("190"),
            unit="mg/dL",
            number=1,
        ),
        _observation(
            person_id=person_id,
            label="LDL",
            value=Decimal("110"),
            unit="mg/dL",
            number=2,
        ),
        _observation(
            person_id=person_id,
            label="HDL",
            value=Decimal("55"),
            unit="mg/dL",
            number=3,
        ),
        _observation(
            person_id=person_id,
            label="三酸甘油脂",
            value=Decimal("120"),
            unit="mg/dL",
            number=4,
        ),
        _observation(
            person_id=person_id,
            label="GPT",
            value=Decimal("24"),
            unit="U/L",
            number=5,
        ),
    ]

    result = build_named_labs_input(
        observations,
        person_id=person_id,
        window_start=NOW - timedelta(days=30),
    )

    assert result.missing_keys == ()
    assert tuple(result.values) == LEGACY_LAB_KEYS
    assert result.value_for("Total Cholesterol").value == Decimal("190")
    assert result.value_for("Triglycerides").value == Decimal("120")
    assert result.value_for("ALT").value == Decimal("24")


def test_unsupported_similar_labels_do_not_resolve() -> None:
    person_id = _uuid(2)
    observations = [
        _observation(
            person_id=person_id,
            label="LDL-C",
            value=Decimal("110"),
            unit="mg/dL",
            number=1,
        ),
        _observation(
            person_id=person_id,
            label="ALT (GPT)",
            value=Decimal("24"),
            unit="U/L",
            number=2,
        ),
        _observation(
            person_id=person_id,
            label="三酸甘油酯",
            value=Decimal("120"),
            unit="mg/dL",
            number=3,
        ),
    ]

    result = build_named_labs_input(observations, person_id=person_id)

    assert result.missing_keys == LEGACY_LAB_KEYS
    assert canonical_legacy_lab_key("LDL-C") is None
    assert canonical_legacy_lab_key("ALT (GPT)") is None


def test_pending_and_text_only_observations_remain_missing() -> None:
    person_id = _uuid(3)
    observations = [
        _observation(
            person_id=person_id,
            label="LDL",
            value=Decimal("110"),
            unit="mg/dL",
            status="pending",
            number=1,
        ),
        _observation(
            person_id=person_id,
            label="HDL",
            value=None,
            unit="mg/dL",
            number=2,
        ),
    ]

    result = build_named_labs_input(observations, person_id=person_id)

    assert result.value_for("LDL") is None
    assert result.value_for("HDL") is None


def test_units_accept_the_legacy_scale_and_only_normalize_iul() -> None:
    person_id = _uuid(4)
    observations = [
        _observation(
            person_id=person_id,
            label="ALT",
            value=Decimal("1.5"),
            unit="IU/L",
            number=1,
        ),
        _observation(
            person_id=person_id,
            label="LDL",
            value=Decimal("3.4"),
            unit="mmol/L",
            number=2,
        ),
        _observation(
            person_id=person_id,
            label="HDL",
            value=Decimal("55"),
            unit=None,
            number=3,
        ),
    ]

    result = build_named_labs_input(observations, person_id=person_id)

    alt = result.value_for("ALT")
    assert alt is not None
    assert alt.value == Decimal("1.5")
    assert alt.unit == "U/L"
    assert result.value_for("LDL") is None
    assert result.value_for("HDL").unit is None


def test_window_and_latest_selection_are_deterministic() -> None:
    person_id = _uuid(5)
    window_start = NOW - timedelta(days=30)
    older = _observation(
        person_id=person_id,
        label="LDL",
        value=Decimal("140"),
        unit="mg/dL",
        observed_at=NOW - timedelta(days=2),
        report_created_at=NOW - timedelta(days=2),
        number=1,
    )
    newer = _observation(
        person_id=person_id,
        label="LDL",
        value=Decimal("110"),
        unit="mg/dL",
        observed_at=NOW - timedelta(days=1),
        report_created_at=NOW - timedelta(days=1),
        number=2,
    )
    outside_window = _observation(
        person_id=person_id,
        label="HDL",
        value=Decimal("60"),
        unit="mg/dL",
        observed_at=NOW - timedelta(days=1),
        report_created_at=window_start - timedelta(seconds=1),
        number=3,
    )

    result = build_named_labs_input(
        [newer, outside_window, older],
        person_id=person_id,
        window_start=window_start,
    )

    assert result.value_for("LDL").value == Decimal("110")
    assert result.value_for("HDL") is None


def test_ties_use_observation_created_at_then_id() -> None:
    person_id = _uuid(6)
    observed_at = NOW - timedelta(days=1)
    created_at = NOW - timedelta(hours=1)
    first = _observation(
        person_id=person_id,
        label="ALT",
        value=Decimal("20"),
        unit="U/L",
        observed_at=observed_at,
        created_at=created_at,
        number=1,
    )
    second = _observation(
        person_id=person_id,
        label="ALT",
        value=Decimal("24"),
        unit="U/L",
        observed_at=observed_at,
        created_at=created_at,
        number=2,
    )

    forward = build_named_labs_input([first, second], person_id=person_id)
    reverse = build_named_labs_input([second, first], person_id=person_id)

    assert forward.value_for("ALT").value == Decimal("24")
    assert reverse.value_for("ALT").value == Decimal("24")
    assert forward.value_for("ALT").evidence.source_id == second.id


def test_evidence_preserves_report_provenance_and_person_scope() -> None:
    person_id = _uuid(7)
    other_person_id = _uuid(8)
    owned = _observation(
        person_id=person_id,
        label="Total Cholesterol",
        value=Decimal("190"),
        unit="mg/dL",
        number=1,
    )
    foreign = _observation(
        person_id=other_person_id,
        label="Total Cholesterol",
        value=Decimal("999"),
        unit="mg/dL",
        number=2,
    )

    result = build_named_labs_input([foreign, owned], person_id=person_id)
    value = result.value_for("Total Cholesterol")

    assert value is not None
    assert value.value == Decimal("190")
    assert value.evidence.source_id == owned.id
    assert value.evidence.report_id == owned.report_id
    assert value.evidence.person_id == person_id
    assert value.evidence.report_source_name == "Report 1"
    assert value.evidence.observed_at == owned.observed_at


def test_application_read_uses_confirmed_person_scope_and_zero_writes(client: TestClient) -> None:
    registration = register(client, email="score-input-owner@example.com")
    assert registration.status_code == 201
    registration_body = registration.json()
    account_id = uuid.UUID(registration_body["account"]["id"])
    person_id = uuid.UUID(registration_body["default_person"]["id"])

    confirmed = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json={
            "schema_version": "healthy.health-report.v1",
            "source_name": "Confirmed lab",
            "reported_at": (NOW - timedelta(days=1)).isoformat(),
            "observations": [
                {
                    "code": "LDL",
                    "display_name": "LDL",
                    "value_numeric": 110,
                    "unit": "mg/dL",
                    "observed_at": (NOW - timedelta(days=1)).isoformat(),
                }
            ],
        },
    )
    assert confirmed.status_code == 201
    confirmed_id = confirmed.json()["id"]
    assert (
        client.post(
            f"/v1/persons/{person_id}/reports/{confirmed_id}/confirm",
            headers=csrf_headers(client),
        ).status_code
        == 200
    )

    pending = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json={
            "schema_version": "healthy.health-report.v1",
            "source_name": "Pending lab",
            "reported_at": NOW.isoformat(),
            "observations": [
                {
                    "code": "HDL",
                    "display_name": "HDL",
                    "value_numeric": 55,
                    "unit": "mg/dL",
                    "observed_at": NOW.isoformat(),
                }
            ],
        },
    )
    assert pending.status_code == 201

    symptom = client.post(
        f"/v1/persons/{person_id}/symptoms",
        headers=csrf_headers(client),
        json={
            "symptom": "Persistent headache",
            "occurred_at": (NOW - timedelta(days=2)).isoformat(),
            "severity": 3,
            "estimated_start_date": "2026-01-01",
            "estimated_duration_days": 240,
        },
    )
    assert symptom.status_code == 201
    symptom_id = uuid.UUID(symptom.json()["id"])

    other = TestClient(client.app, base_url=ORIGIN)
    other_registration = register(other, email="score-input-other@example.com")
    assert other_registration.status_code == 201
    other_person_id = other_registration.json()["default_person"]["id"]
    other_report = other.post(
        f"/v1/persons/{other_person_id}/reports",
        headers=csrf_headers(other),
        json={
            "schema_version": "healthy.health-report.v1",
            "source_name": "Other person's lab",
            "reported_at": NOW.isoformat(),
            "observations": [
                {
                    "code": "ALT",
                    "display_name": "ALT",
                    "value_numeric": 24,
                    "unit": "U/L",
                    "observed_at": NOW.isoformat(),
                }
            ],
        },
    )
    assert other_report.status_code == 201
    other_report_id = other_report.json()["id"]
    assert (
        other.post(
            f"/v1/persons/{other_person_id}/reports/{other_report_id}/confirm",
            headers=csrf_headers(other),
        ).status_code
        == 200
    )
    other_symptom = other.post(
        f"/v1/persons/{other_person_id}/symptoms",
        headers=csrf_headers(other),
        json={
            "symptom": "Other person's symptom",
            "occurred_at": (NOW - timedelta(days=1)).isoformat(),
            "severity": 4,
            "estimated_duration_days": 365,
        },
    )
    assert other_symptom.status_code == 201

    database = Database(DATABASE_URL)

    def snapshot() -> tuple[list[tuple[object, ...]], ...]:
        with next(database.sessions()) as database_session:
            return (
                list(
                    database_session.execute(
                        select(Person.id, Person.updated_at).order_by(Person.id)
                    ).tuples()
                ),
                list(
                    database_session.execute(
                        select(
                            HealthReportModel.id,
                            HealthReportModel.status,
                            HealthReportModel.confirmed_at,
                        ).order_by(HealthReportModel.id)
                    ).tuples()
                ),
                list(
                    database_session.execute(
                        select(
                            HealthReportObservationModel.id,
                            HealthReportObservationModel.created_at,
                        ).order_by(HealthReportObservationModel.id)
                    ).tuples()
                ),
                list(
                    database_session.execute(
                        select(
                            SymptomLog.id,
                            SymptomLog.person_id,
                            SymptomLog.estimated_duration_days,
                            SymptomLog.created_at,
                        ).order_by(SymptomLog.id)
                    ).tuples()
                ),
            )

    before = snapshot()
    with next(database.sessions()) as database_session:
        result = services.get_health_score_inputs(
            database_session,
            owner_account_id=account_id,
            person_id=person_id,
            now=NOW,
        )
    after = snapshot()
    database.engine.dispose()

    assert after == before
    assert result is not None
    assert result.named_labs.value_for("LDL").value == Decimal("110.0000")
    assert result.named_labs.value_for("HDL") is None
    assert result.named_labs.value_for("ALT") is None
    assert result.symptom_duration.status == "available"
    assert result.symptom_duration.long_term_symptom_count == 1
    assert [observation.id for observation in result.symptom_duration.long_term_symptoms] == [
        symptom_id
    ]


def test_symptom_duration_is_missing_without_persisted_timing_facts() -> None:
    result = build_health_score_inputs([], person_id=_uuid(9), now=NOW)

    assert result.symptom_duration.status == "missing"
    assert result.symptom_duration.unit == "days"
    assert result.symptom_duration.symptom_count == 0
    assert result.symptom_duration.long_term_symptom_count == 0
    assert result.symptom_duration.missing_symptom_ids == ()
