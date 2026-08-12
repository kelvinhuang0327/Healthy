from __future__ import annotations

from datetime import UTC, datetime

from conftest import csrf_headers, register
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _risk_alerts_url(person_id: str) -> str:
    return f"/v1/persons/{person_id}/risk-alerts"


def _create_metric(client: TestClient, person_id: str, **overrides: object):
    payload: dict[str, object] = {
        "recorded_at": NOW.isoformat(),
        "heart_rate_bpm": 72,
    }
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json=payload,
    )


def _create_confirmed_report(client: TestClient, person_id: str) -> tuple[dict, dict]:
    imported = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json={
            "schema_version": "healthy.health-report.v1",
            "source_name": "Confirmed Risk Lab",
            "reported_at": NOW.isoformat(),
            "observations": [
                {
                    "code": "ALT",
                    "display_name": "ALT",
                    "value_numeric": 50,
                    "unit": "U/L",
                    "observed_at": NOW.isoformat(),
                }
            ],
        },
    )
    assert imported.status_code == 201
    imported_body = imported.json()
    confirmed = client.post(
        f"/v1/persons/{person_id}/reports/{imported_body['id']}/confirm",
        headers=csrf_headers(client),
    )
    assert confirmed.status_code == 200
    return imported_body, confirmed.json()


def test_empty_alert_response_is_explicit_and_safe(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    response = client.get(_risk_alerts_url(person_id))

    assert response.status_code == 200
    assert response.json() == {"active_count": 0, "alerts": []}


def test_metric_alert_preserves_provenance_and_active_count(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    metric = _create_metric(
        client,
        person_id,
        systolic_bp_mm_hg=145,
        diastolic_bp_mm_hg=95,
    )
    assert metric.status_code == 201

    response = client.get(_risk_alerts_url(person_id))

    assert response.status_code == 200
    body = response.json()
    assert body["active_count"] == 1
    assert body["alerts"] == [
        {
            "rule_code": "BP_HIGH",
            "risk_type": "bp_high",
            "severity": "high",
            "status": "active",
            "evidence": {
                "source_kind": "health_metric",
                "source_id": metric.json()["id"],
                "person_id": person_id,
                "observed_at": NOW.isoformat().replace("+00:00", "Z"),
                "observation_id": None,
                "report_id": None,
                "report_source_name": None,
            },
        }
    ]


def test_confirmed_lab_alert_preserves_report_provenance(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    report, _confirmed = _create_confirmed_report(client, person_id)
    observation_id = report["observations"][0]["id"]

    response = client.get(_risk_alerts_url(person_id))

    assert response.status_code == 200
    body = response.json()
    assert body["active_count"] == 1
    alert = body["alerts"][0]
    assert alert["rule_code"] == "LIVER_ALT_HIGH"
    assert alert["risk_type"] == "liver_alt_high"
    assert alert["severity"] == "medium"
    assert alert["evidence"] == {
        "source_kind": "lab_report",
        "source_id": report["id"],
        "person_id": person_id,
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "observation_id": observation_id,
        "report_id": report["id"],
        "report_source_name": "Confirmed Risk Lab",
    }


def test_foreign_person_cannot_read_risk_alerts(client: TestClient) -> None:
    assert register(client, email="risk-owner-a@example.com").status_code == 201
    person_a = _person_id(client)
    metric = _create_metric(
        client,
        person_a,
        systolic_bp_mm_hg=145,
        diastolic_bp_mm_hg=95,
    )
    assert metric.status_code == 201

    other = TestClient(client.app, base_url="http://127.0.0.1:3000")
    assert register(other, email="risk-owner-b@example.com").status_code == 201

    response = other.get(_risk_alerts_url(person_a))

    assert response.status_code == 404
    assert response.json() == {"detail": "Person not found"}
    assert person_a not in response.text
    assert metric.json()["id"] not in response.text


def test_get_risk_alerts_performs_zero_writes(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    metric = _create_metric(
        client,
        person_id,
        systolic_bp_mm_hg=145,
        diastolic_bp_mm_hg=95,
    )
    assert metric.status_code == 201

    commit_sessions: list[Session] = []

    def record_commit(session: Session) -> None:
        commit_sessions.append(session)

    event.listen(Session, "after_commit", record_commit)
    try:
        first = client.get(_risk_alerts_url(person_id))
        second = client.get(_risk_alerts_url(person_id))
    finally:
        event.remove(Session, "after_commit", record_commit)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert commit_sessions == []


def test_existing_metric_thresholds_are_exposed_without_rule_changes(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    metric = _create_metric(
        client,
        person_id,
        systolic_bp_mm_hg=130,
        diastolic_bp_mm_hg=80,
        blood_glucose_mg_dl=126,
    )
    assert metric.status_code == 201

    response = client.get(_risk_alerts_url(person_id))

    assert response.status_code == 200
    body = response.json()
    assert body["active_count"] == 2
    assert [alert["rule_code"] for alert in body["alerts"]] == [
        "BP_HIGH",
        "GLUCOSE_HIGH",
    ]
