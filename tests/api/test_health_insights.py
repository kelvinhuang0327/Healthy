from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import DATABASE_URL, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import (
    HealthMetric,
    HealthReportModel,
    HealthReportObservationModel,
    SymptomLog,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _today_url(person_id: str) -> str:
    return f"/v1/persons/{person_id}/assistant/today"


def _create_metric(
    client: TestClient,
    person_id: str,
    *,
    recorded_at: datetime,
    weight_kg: float,
) -> str:
    response = client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json={"recorded_at": recorded_at.isoformat(), "weight_kg": weight_kg},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _create_symptom(
    client: TestClient,
    person_id: str,
    *,
    occurred_at: datetime,
) -> str:
    response = client.post(
        f"/v1/persons/{person_id}/symptoms",
        headers=csrf_headers(client),
        json={
            "symptom": "Headache",
            "occurred_at": occurred_at.isoformat(),
            "severity": 2,
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def _report_payload(*, source_name: str, code: str) -> dict[str, object]:
    observed_at = datetime.now(UTC).isoformat()
    return {
        "schema_version": "healthy.health-report.v1",
        "source_name": source_name,
        "reported_at": observed_at,
        "observations": [
            {
                "code": code,
                "display_name": "Hemoglobin A1c",
                "value_numeric": 5.4,
                "unit": "%",
                "observed_at": observed_at,
            }
        ],
    }


def test_insights_are_deterministic_and_link_only_allowed_evidence(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    now = datetime.now(UTC)

    previous_metric_id = _create_metric(
        client,
        person_id,
        recorded_at=now - timedelta(days=2),
        weight_kg=72.4,
    )
    latest_metric_id = _create_metric(
        client,
        person_id,
        recorded_at=now - timedelta(days=1),
        weight_kg=71.8,
    )
    first_symptom_id = _create_symptom(
        client,
        person_id,
        occurred_at=now - timedelta(days=2, hours=1),
    )
    latest_symptom_id = _create_symptom(
        client,
        person_id,
        occurred_at=now - timedelta(hours=12),
    )

    pending = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json=_report_payload(source_name="Pending Lab", code="PENDING_HBA1C"),
    )
    assert pending.status_code == 201
    pending_report_id = pending.json()["id"]

    before_confirmation = client.get(_today_url(person_id))
    assert before_confirmation.status_code == 200
    before_body = before_confirmation.json()
    assert [item["insight_type"] for item in before_body["insights"]] == [
        "symptom_pattern",
        "metric_change",
    ]
    assert before_body["insights"][1]["headline"] == "Weight changed from 72.4 kg to 71.8 kg."
    assert {
        evidence["source_record_id"] for evidence in before_body["insights"][1]["evidence"]
    } == {
        previous_metric_id,
        latest_metric_id,
    }
    assert before_body["insights"][0]["headline"] == (
        "Headache appears in 2 recorded symptom entries."
    )
    assert {
        evidence["source_record_id"] for evidence in before_body["insights"][0]["evidence"]
    } == {
        first_symptom_id,
        latest_symptom_id,
    }
    assert "PENDING_HBA1C" not in str(before_body["insights"])

    confirm = client.post(
        f"/v1/persons/{person_id}/reports/{pending_report_id}/confirm",
        headers=csrf_headers(client),
    )
    assert confirm.status_code == 200

    after_confirmation = client.get(_today_url(person_id))
    assert after_confirmation.status_code == 200
    after_body = after_confirmation.json()
    assert len(after_body["insights"]) == 3
    report_insight = next(
        item
        for item in after_body["insights"]
        if item["insight_type"] == "report_observation_update"
    )
    assert report_insight["headline"] == "Latest confirmed report records Hemoglobin A1c: 5.4 %."
    assert report_insight["evidence"][0]["report_id"] == pending_report_id
    assert report_insight["evidence"][0]["report_source_name"] == "Pending Lab"

    repeated = client.get(_today_url(person_id)).json()
    assert [item["id"] for item in repeated["insights"]] == [
        item["id"] for item in after_body["insights"]
    ]
    assert [item["headline"] for item in repeated["insights"]] == [
        item["headline"] for item in after_body["insights"]
    ]


def test_insights_get_is_zero_write_and_pending_reports_are_ignored(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    now = datetime.now(UTC)
    _create_metric(client, person_id, recorded_at=now - timedelta(days=1), weight_kg=72.4)
    _create_metric(client, person_id, recorded_at=now, weight_kg=71.8)
    pending = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json=_report_payload(source_name="Pending Only Lab", code="PENDING_ONLY"),
    )
    assert pending.status_code == 201

    database = Database(DATABASE_URL)

    def snapshot() -> tuple[list[tuple[object, ...]], ...]:
        with Session(database.engine) as database_session:
            return (
                list(
                    database_session.execute(
                        select(HealthMetric.id, HealthMetric.created_at)
                    ).tuples()
                ),
                list(
                    database_session.execute(select(SymptomLog.id, SymptomLog.created_at)).tuples()
                ),
                list(
                    database_session.execute(
                        select(
                            HealthReportModel.id,
                            HealthReportModel.status,
                            HealthReportModel.confirmed_at,
                        )
                    ).tuples()
                ),
                list(database_session.execute(select(HealthReportObservationModel.id)).tuples()),
            )

    before = snapshot()
    first = client.get(_today_url(person_id))
    second = client.get(_today_url(person_id))
    assert first.status_code == second.status_code == 200
    assert first.json()["insights"] == second.json()["insights"]
    assert first.json()["insights"][0]["insight_type"] == "metric_change"
    assert snapshot() == before
