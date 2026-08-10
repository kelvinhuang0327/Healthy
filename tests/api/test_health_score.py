from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import csrf_headers, register
from fastapi.testclient import TestClient


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def test_health_score_is_insufficient_without_scored_data(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    response = client.get(f"/v1/persons/{person_id}/health-score")

    assert response.status_code == 200
    assert response.json() == {
        "score": None,
        "status": "insufficient_data",
        "rule_version": "deterministic-health-score-v1",
        "anchor_at": None,
        "data_points": 0,
        "components": [],
        "limitations": (
            "Add a blood pressure, heart rate, blood glucose, or symptom record. "
            "This score is a non-diagnostic product signal, not medical advice."
        ),
    }


def test_health_score_is_deterministic_and_explains_latest_evidence(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 1, 2, tzinfo=UTC)
    old_metric = client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json={"recorded_at": older.isoformat(), "heart_rate_bpm": 250},
    )
    latest_metric = client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json={
            "recorded_at": newer.isoformat(),
            "systolic_bp_mm_hg": 120,
            "diastolic_bp_mm_hg": 80,
            "heart_rate_bpm": 72,
            "blood_glucose_mg_dl": 95.0,
            "weight_kg": 70.0,
        },
    )
    assert old_metric.status_code == latest_metric.status_code == 201
    symptom = client.post(
        f"/v1/persons/{person_id}/symptoms",
        headers=csrf_headers(client),
        json={
            "symptom": "Headache",
            "occurred_at": (newer + timedelta(days=1)).isoformat(),
            "severity": 3,
        },
    )
    assert symptom.status_code == 201

    first = client.get(f"/v1/persons/{person_id}/health-score")
    second = client.get(f"/v1/persons/{person_id}/health-score")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    body = first.json()
    assert body["status"] == "stable"
    assert body["score"] == 98
    assert body["data_points"] == 3
    assert [component["kind"] for component in body["components"]] == [
        "blood_pressure",
        "heart_rate",
        "blood_glucose",
        "recent_symptoms",
    ]
    assert body["components"][0]["evidence_ids"] == [latest_metric.json()["id"]]
    assert body["components"][3]["evidence_ids"] == [symptom.json()["id"]]


def test_health_score_does_not_score_weight_without_personal_context(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    created = client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json={"recorded_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(), "weight_kg": 70},
    )
    assert created.status_code == 201

    body = client.get(f"/v1/persons/{person_id}/health-score").json()

    assert body["score"] is None
    assert body["status"] == "insufficient_data"
    assert body["data_points"] == 1


def test_health_score_is_owner_scoped(client: TestClient) -> None:
    assert register(client, email="owner-a@example.com").status_code == 201
    person_id = _person_id(client)
    other = TestClient(client.app, base_url="http://127.0.0.1:3000")
    assert register(other, email="owner-b@example.com").status_code == 201

    response = other.get(f"/v1/persons/{person_id}/health-score")

    assert response.status_code == 404
