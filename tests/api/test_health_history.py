from __future__ import annotations

from datetime import UTC, datetime

from conftest import DATABASE_URL, ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import (
    HealthMetric,
    HealthReportModel,
    HealthReportObservationModel,
    Person,
    SessionRecord,
    SymptomLog,
)
from sqlalchemy import select


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _create_metric(client: TestClient, person_id: str, **overrides: object):
    payload = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "systolic_bp_mm_hg": None,
        "diastolic_bp_mm_hg": None,
        "heart_rate_bpm": None,
        "weight_kg": None,
        "blood_glucose_mg_dl": None,
        "note": None,
    }
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json=payload,
    )


def _create_symptom(client: TestClient, person_id: str, **overrides: object):
    payload = {
        "symptom": "Headache",
        "occurred_at": datetime.now(UTC).isoformat(),
        "severity": 3,
        "duration_minutes": None,
        "note": None,
    }
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/symptoms",
        headers=csrf_headers(client),
        json=payload,
    )


def _report_payload(
    *,
    source_name: str,
    reported_at: str,
    code: str,
    display_name: str,
    observed_at: str,
    value_numeric: float,
) -> dict[str, object]:
    return {
        "schema_version": "healthy.health-report.v1",
        "source_name": source_name,
        "reported_at": reported_at,
        "observations": [
            {
                "code": code,
                "display_name": display_name,
                "value_numeric": value_numeric,
                "unit": "mg/dL",
                "reference_range": "70-99",
                "observed_at": observed_at,
            }
        ],
    }


def _create_confirmed_report(
    client: TestClient,
    person_id: str,
    *,
    source_name: str,
    reported_at: str,
    code: str,
    display_name: str,
    observed_at: str,
    value_numeric: float,
) -> tuple[dict[str, object], dict[str, object]]:
    imported = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json=_report_payload(
            source_name=source_name,
            reported_at=reported_at,
            code=code,
            display_name=display_name,
            observed_at=observed_at,
            value_numeric=value_numeric,
        ),
    )
    assert imported.status_code == 201
    report = imported.json()
    confirmed = client.post(
        f"/v1/persons/{person_id}/reports/{report['id']}/confirm",
        headers=csrf_headers(client),
    )
    assert confirmed.status_code == 200
    return report, confirmed.json()


def test_empty_history_returns_stable_empty_collection(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    assert client.get(f"/v1/persons/{person_id}/history").json() == []
    assert client.get(f"/v1/persons/{person_id}/history").json() == []


def test_history_mixes_sources_newest_first_and_excludes_pending_reports(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    symptom = _create_symptom(
        client,
        person_id,
        symptom="Backdated headache",
        occurred_at="2026-08-03T08:00:00Z",
        severity=3,
        note="After breakfast",
    )
    metric = _create_metric(
        client,
        person_id,
        recorded_at="2026-08-02T08:00:00Z",
        heart_rate_bpm=72,
    )
    pending = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json=_report_payload(
            source_name="Pending Lab",
            reported_at="2026-08-04T08:00:00Z",
            code="PENDING_GLUCOSE",
            display_name="Pending glucose",
            observed_at="2026-08-04T08:00:00Z",
            value_numeric=101.0,
        ),
    )
    confirmed, confirmed_detail = _create_confirmed_report(
        client,
        person_id,
        source_name="Confirmed Lab",
        reported_at="2026-08-05T08:00:00Z",
        code="CONFIRMED_GLUCOSE",
        display_name="Confirmed glucose",
        observed_at="2026-08-05T08:00:00Z",
        value_numeric=95.5,
    )
    assert symptom.status_code == metric.status_code == 201
    assert pending.status_code == 201

    response = client.get(f"/v1/persons/{person_id}/history")
    assert response.status_code == 200
    history = response.json()

    assert [item["kind"] for item in history] == [
        "report_observation",
        "symptom",
        "metric",
    ]
    assert len(history) == 3
    assert "PENDING_GLUCOSE" not in response.text
    assert "raw_json" not in response.text

    report_item, symptom_item, metric_item = history
    assert report_item["title"] == "Confirmed glucose"
    assert report_item["primary_value"] == "95.5"
    assert report_item["unit"] == "mg/dL"
    assert report_item["source"] == {
        "type": "report_observation",
        "id": confirmed["observations"][0]["id"],
        "report_id": confirmed["id"],
        "report_source_name": "Confirmed Lab",
    }
    assert symptom_item["primary_value"] == "Backdated headache"
    assert symptom_item["detail"] == "Severity 3/5 · After breakfast"
    assert symptom_item["source"] == {
        "type": "symptom",
        "id": symptom.json()["id"],
        "report_id": None,
        "report_source_name": None,
    }
    assert metric_item["primary_value"] == "72 bpm"
    assert metric_item["source"]["type"] == "metric"
    assert metric_item["source"]["id"] == metric.json()["id"]
    assert confirmed_detail["status"] == "confirmed"
    assert confirmed["id"] == confirmed_detail["id"] == report_item["source"]["report_id"]


def test_history_tied_timestamps_are_deterministic_across_reads(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    tied_at = "2026-08-06T08:00:00Z"
    assert (
        _create_metric(
            client,
            person_id,
            recorded_at=tied_at,
            heart_rate_bpm=70,
        ).status_code
        == 201
    )
    assert (
        _create_symptom(
            client,
            person_id,
            symptom="Tied symptom",
            occurred_at=tied_at,
        ).status_code
        == 201
    )
    _create_confirmed_report(
        client,
        person_id,
        source_name="Tied Lab",
        reported_at=tied_at,
        code="TIED_VALUE",
        display_name="Tied value",
        observed_at=tied_at,
        value_numeric=88.0,
    )

    first = client.get(f"/v1/persons/{person_id}/history")
    second = client.get(f"/v1/persons/{person_id}/history")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(first.json()) == 3


def test_history_preserves_person_isolation(client: TestClient) -> None:
    assert register(client, email="history-owner-a@example.com").status_code == 201
    person_a = _person_id(client)
    created = _create_symptom(client, person_a, symptom="Owner A symptom")
    assert created.status_code == 201

    other = TestClient(client.app, base_url=ORIGIN)
    assert register(other, email="history-owner-b@example.com").status_code == 201
    person_b = _person_id(other)
    assert other.get(f"/v1/persons/{person_a}/history").status_code == 404
    assert other.get(f"/v1/persons/{person_b}/history").json() == []
    assert client.get(f"/v1/persons/{person_a}/history").json()[0]["primary_value"] == (
        "Owner A symptom"
    )


def test_history_get_is_zero_write_and_does_not_change_today_semantics(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    assert _create_metric(client, person_id, heart_rate_bpm=72).status_code == 201
    assert _create_symptom(client, person_id, symptom="Stable symptom").status_code == 201
    _create_confirmed_report(
        client,
        person_id,
        source_name="Stable Lab",
        reported_at="2026-08-07T08:00:00Z",
        code="STABLE_VALUE",
        display_name="Stable value",
        observed_at="2026-08-07T08:00:00Z",
        value_numeric=90.0,
    )

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
                        select(SessionRecord.id, SessionRecord.expires_at).order_by(
                            SessionRecord.id
                        )
                    ).tuples()
                ),
                list(
                    database_session.execute(
                        select(
                            HealthMetric.id,
                            HealthMetric.recorded_at,
                            HealthMetric.created_at,
                            HealthMetric.heart_rate_bpm,
                        ).order_by(HealthMetric.id)
                    ).tuples()
                ),
                list(
                    database_session.execute(
                        select(
                            SymptomLog.id,
                            SymptomLog.occurred_at,
                            SymptomLog.created_at,
                            SymptomLog.symptom,
                        ).order_by(SymptomLog.id)
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
                            HealthReportObservationModel.observed_at,
                            HealthReportObservationModel.created_at,
                        ).order_by(HealthReportObservationModel.id)
                    ).tuples()
                ),
            )

    before = snapshot()
    today_before = client.get(f"/v1/persons/{person_id}/assistant/today").json()
    assert client.get(f"/v1/persons/{person_id}/history").status_code == 200
    assert client.get(f"/v1/persons/{person_id}/history").status_code == 200
    today_after = client.get(f"/v1/persons/{person_id}/assistant/today").json()
    after = snapshot()

    assert after == before
    today_before.pop("generated_at")
    today_after.pop("generated_at")
    assert today_after == today_before
