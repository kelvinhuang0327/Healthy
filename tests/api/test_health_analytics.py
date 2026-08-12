from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import DATABASE_URL, ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import HealthMetric, Person
from httpx import Response
from sqlalchemy import select


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _create_metric(client: TestClient, person_id: str, **overrides: object):
    payload = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "systolic_bp_mm_hg": None,
        "diastolic_bp_mm_hg": None,
        "heart_rate_bpm": None,
        "steps": None,
        "weight_kg": None,
        "blood_glucose_mg_dl": None,
        "sleep_hours": None,
        "note": None,
    }
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json=payload,
    )


def _summary(response: Response, metric: str) -> dict[str, object]:
    return next(item for item in response.json()["summaries"] if item["metric"] == metric)


def test_empty_analytics_returns_fixed_no_data_summaries(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    response = client.get(f"/v1/persons/{person_id}/analytics")

    assert response.status_code == 200
    assert response.json()["period_days"] == 90
    assert [item["metric"] for item in response.json()["summaries"]] == [
        "systolic_bp_mm_hg",
        "diastolic_bp_mm_hg",
        "heart_rate_bpm",
        "steps",
        "weight_kg",
        "blood_glucose_mg_dl",
        "sleep_hours",
    ]
    assert all(
        item["points"] == 0 and item["direction"] == "no_data"
        for item in response.json()["summaries"]
    )


def test_analytics_summarizes_recent_values_excludes_old_values_and_is_deterministic(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    now = datetime.now(UTC)
    first_metric = _create_metric(
        client,
        person_id,
        recorded_at=(now - timedelta(days=10)).isoformat(),
        systolic_bp_mm_hg=120,
        diastolic_bp_mm_hg=80,
        weight_kg=80,
        blood_glucose_mg_dl=100,
    )
    assert first_metric.status_code == 201, first_metric.text
    second_metric = _create_metric(
        client,
        person_id,
        recorded_at=(now - timedelta(days=5)).isoformat(),
        systolic_bp_mm_hg=126,
        diastolic_bp_mm_hg=82,
        weight_kg=84,
        blood_glucose_mg_dl=90,
    )
    assert second_metric.status_code == 201, second_metric.text
    old_metric = _create_metric(
        client,
        person_id,
        recorded_at=(now - timedelta(days=60)).isoformat(),
        weight_kg=50,
    )
    assert old_metric.status_code == 201, old_metric.text

    response = client.get(f"/v1/persons/{person_id}/analytics?days=30")
    repeat = client.get(f"/v1/persons/{person_id}/analytics?days=30")

    assert response.status_code == repeat.status_code == 200
    assert response.json() == repeat.json()
    assert response.json()["period_days"] == 30
    assert _summary(response, "systolic_bp_mm_hg") == {
        "metric": "systolic_bp_mm_hg",
        "label": "Systolic blood pressure",
        "unit": "mmHg",
        "points": 2,
        "first_value": 120.0,
        "last_value": 126.0,
        "change_percent": 5.0,
        "slope_per_day": 1.2,
        "direction": "up",
    }
    assert _summary(response, "weight_kg")["points"] == 2
    assert _summary(response, "weight_kg")["direction"] == "up"
    assert _summary(response, "blood_glucose_mg_dl")["direction"] == "down"


def test_analytics_preserves_person_isolation(client: TestClient) -> None:
    assert register(client, email="analytics-owner-a@example.com").status_code == 201
    person_a = _person_id(client)
    assert _create_metric(client, person_a, heart_rate_bpm=72).status_code == 201

    other = TestClient(client.app, base_url=ORIGIN)
    assert register(other, email="analytics-owner-b@example.com").status_code == 201
    person_b = _person_id(other)

    assert other.get(f"/v1/persons/{person_a}/analytics").status_code == 404
    assert all(
        item["points"] == 0
        for item in other.get(f"/v1/persons/{person_b}/analytics").json()["summaries"]
    )


def test_analytics_get_is_zero_write(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    assert _create_metric(client, person_id, heart_rate_bpm=72).status_code == 201

    database = Database(DATABASE_URL)

    def snapshot() -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
        with next(database.sessions()) as database_session:
            return (
                list(
                    database_session.execute(
                        select(Person.id, Person.updated_at).order_by(Person.id)
                    ).tuples()
                ),
                list(
                    database_session.execute(
                        select(HealthMetric.id, HealthMetric.created_at).order_by(HealthMetric.id)
                    ).tuples()
                ),
            )

    before = snapshot()
    assert client.get(f"/v1/persons/{person_id}/analytics").status_code == 200
    after = snapshot()

    assert after == before
