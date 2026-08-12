from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID

from conftest import DATABASE_URL, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.application import services
from healthy.domain.action_recommendations import recommendation_identity_fingerprint
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import Account, HealthAction, HealthMetric
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _recommendations_url(person_id: str) -> str:
    return f"/v1/persons/{person_id}/action-recommendations"


def _acceptance_url(person_id: str, recommendation_code: str) -> str:
    return f"{_recommendations_url(person_id)}/{recommendation_code}/accept"


def _create_metric(
    client: TestClient,
    person_id: str,
    *,
    recorded_at: datetime = NOW,
    **overrides: object,
):
    payload: dict[str, object] = {
        "recorded_at": recorded_at.isoformat(),
        "systolic_bp_mm_hg": 145,
        "diastolic_bp_mm_hg": 95,
        "heart_rate_bpm": 72,
    }
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json=payload,
    )


def _create_confirmed_report(client: TestClient, person_id: str) -> dict:
    imported = client.post(
        f"/v1/persons/{person_id}/reports",
        headers=csrf_headers(client),
        json={
            "schema_version": "healthy.health-report.v1",
            "source_name": "Acceptance Lab",
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
    report = imported.json()
    confirmed = client.post(
        f"/v1/persons/{person_id}/reports/{report['id']}/confirm",
        headers=csrf_headers(client),
    )
    assert confirmed.status_code == 200
    return confirmed.json()


def _current_recommendation(client: TestClient, person_id: str) -> dict:
    response = client.get(_recommendations_url(person_id))
    assert response.status_code == 200
    recommendations = response.json()["recommendations"]
    assert len(recommendations) == 1
    return recommendations[0]


def _acceptance_payload(recommendation: dict) -> dict[str, object]:
    evidence = recommendation["evidence"]
    return {
        "rule_version": recommendation["rule_version"],
        "source_kind": evidence["source_kind"],
        "source_id": evidence["source_id"],
        "observation_id": evidence["observation_id"],
        "report_id": evidence["report_id"],
        "observed_at": evidence["observed_at"],
    }


def _accept(client: TestClient, person_id: str, recommendation: dict, **overrides: object):
    payload = _acceptance_payload(recommendation)
    payload.update(overrides)
    return client.post(
        _acceptance_url(person_id, recommendation["recommendation_code"]),
        headers=csrf_headers(client),
        json=payload,
    )


def test_fingerprint_is_canonical_and_person_scoped() -> None:
    source_id = UUID("00000000-0000-0000-0000-000000000010")
    observation_id = UUID("00000000-0000-0000-0000-000000000011")
    report_id = UUID("00000000-0000-0000-0000-000000000012")
    person_id = UUID("00000000-0000-0000-0000-000000000001")
    identity = {
        "person_id": person_id,
        "recommendation_code": "REVIEW_BP_HIGH",
        "rule_version": "risk-action-recommendations-v1",
        "source_kind": "health_metric",
        "source_id": source_id,
        "observation_id": observation_id,
        "report_id": report_id,
    }

    first = recommendation_identity_fingerprint(**identity)  # type: ignore[arg-type]
    second = recommendation_identity_fingerprint(**identity)  # type: ignore[arg-type]
    other_person = recommendation_identity_fingerprint(
        **{**identity, "person_id": UUID("00000000-0000-0000-0000-000000000002")}
    )  # type: ignore[arg-type]

    assert first == second
    assert len(first) == 64
    assert first != other_person


def test_first_acceptance_persists_server_owned_structured_provenance(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    metric = _create_metric(client, person_id)
    assert metric.status_code == 201
    recommendation = _current_recommendation(client, person_id)

    response = _accept(client, person_id, recommendation)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    action = body["action"]
    assert action["title"] == "Review: Blood pressure signal"
    assert action["description"] == recommendation["suggested_action"]
    assert action["due_at"] is None
    assert action["origin_type"] == "action_recommendation"
    assert action["recommendation_code"] == recommendation["recommendation_code"]
    assert action["recommendation_rule_version"] == recommendation["rule_version"]
    assert action["source_rule_code"] == recommendation["source_rule_code"]
    assert action["source_evidence_kind"] == "health_metric"
    assert action["source_evidence_id"] == metric.json()["id"]
    assert action["source_observation_id"] is None
    assert action["source_report_id"] is None
    assert action["source_evidence_observed_at"] == recommendation["evidence"]["observed_at"]
    assert "recommendation_fingerprint" not in action

    actions = client.get(f"/v1/persons/{person_id}/actions")
    assert actions.status_code == 200
    assert [row["id"] for row in actions.json()] == [action["id"]]


def test_lab_acceptance_retains_observation_and_report_provenance(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    report = _create_confirmed_report(client, person_id)
    recommendation = _current_recommendation(client, person_id)
    assert recommendation["evidence"]["source_kind"] == "lab_report"

    response = _accept(client, person_id, recommendation)

    assert response.status_code == 200
    action = response.json()["action"]
    assert action["source_rule_code"] == "LIVER_ALT_HIGH"
    assert action["source_evidence_kind"] == "lab_report"
    assert action["source_evidence_id"] == report["id"]
    assert action["source_observation_id"] == report["observations"][0]["id"]
    assert action["source_report_id"] == report["id"]
    assert action["source_evidence_observed_at"] == NOW.isoformat().replace("+00:00", "Z")


def test_exact_retry_returns_same_action_even_after_current_recommendation_changes(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    assert _create_metric(client, person_id).status_code == 201
    recommendation = _current_recommendation(client, person_id)
    first = _accept(client, person_id, recommendation)
    assert first.status_code == 200

    newer_metric = _create_metric(
        client,
        person_id,
        recorded_at=NOW + timedelta(minutes=1),
        systolic_bp_mm_hg=150,
        diastolic_bp_mm_hg=96,
    )
    assert newer_metric.status_code == 201

    retry = _accept(client, person_id, recommendation)

    assert retry.status_code == 200
    assert retry.json()["created"] is False
    assert retry.json()["action"]["id"] == first.json()["action"]["id"]
    assert len(client.get(f"/v1/persons/{person_id}/actions").json()) == 1


def test_stale_changed_recommendation_returns_conflict_without_creating_action(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    assert _create_metric(client, person_id).status_code == 201
    stale = _current_recommendation(client, person_id)
    assert (
        _create_metric(
            client,
            person_id,
            recorded_at=NOW + timedelta(minutes=1),
            systolic_bp_mm_hg=151,
            diastolic_bp_mm_hg=97,
        ).status_code
        == 201
    )

    response = _accept(client, person_id, stale)

    assert response.status_code == 409
    assert response.json() == {"detail": "Recommendation is no longer current"}
    assert client.get(f"/v1/persons/{person_id}/actions").json() == []


def test_disappeared_recommendation_returns_conflict_without_creating_action(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    metric = _create_metric(client, person_id)
    assert metric.status_code == 201
    stale = _current_recommendation(client, person_id)
    database = Database(DATABASE_URL)
    with Session(database.engine) as database_session:
        database_session.execute(
            delete(HealthMetric).where(HealthMetric.id == UUID(metric.json()["id"]))
        )
        database_session.commit()

    response = _accept(client, person_id, stale)

    assert response.status_code == 409
    assert client.get(f"/v1/persons/{person_id}/actions").json() == []


def test_acceptance_rejects_client_action_content_and_keeps_due_at_server_owned(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    assert _create_metric(client, person_id).status_code == 201
    recommendation = _current_recommendation(client, person_id)

    response = _accept(
        client,
        person_id,
        recommendation,
        title="Client-controlled title",
        description="Client-controlled description",
        due_at="2026-08-12T09:00:00Z",
    )

    assert response.status_code == 422
    assert client.get(f"/v1/persons/{person_id}/actions").json() == []


def test_foreign_person_cannot_accept_recommendation(client: TestClient) -> None:
    assert register(client, email="acceptance-owner-a@example.com").status_code == 201
    person_a = _person_id(client)
    assert _create_metric(client, person_a).status_code == 201
    recommendation = _current_recommendation(client, person_a)

    other = TestClient(client.app, base_url="http://127.0.0.1:3000")
    assert register(other, email="acceptance-owner-b@example.com").status_code == 201
    response = _accept(other, person_a, recommendation)

    assert response.status_code == 404
    assert response.json() == {"detail": "Person not found"}


def test_concurrent_same_acceptance_returns_one_action_and_one_db_row(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = UUID(_person_id(client))
    assert _create_metric(client, str(person_id)).status_code == 201
    recommendation = _current_recommendation(client, str(person_id))
    database = Database(DATABASE_URL)
    with Session(database.engine) as database_session:
        owner_account_id = database_session.scalar(select(Account.id))
    assert owner_account_id is not None
    evidence = recommendation["evidence"]
    observed_at = datetime.fromisoformat(evidence["observed_at"].replace("Z", "+00:00"))

    def accept_once() -> tuple[UUID, bool]:
        with Session(database.engine) as database_session:
            result = services.accept_action_recommendation(
                database_session,
                owner_account_id=owner_account_id,
                person_id=person_id,
                recommendation_code=recommendation["recommendation_code"],
                rule_version=recommendation["rule_version"],
                source_kind=evidence["source_kind"],
                source_id=UUID(evidence["source_id"]),
                observation_id=None,
                report_id=None,
                observed_at=observed_at,
            )
            assert result is not None
            return result.action.id, result.created

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: accept_once(), range(2)))

    assert {created for _, created in results} == {True, False}
    assert len({action_id for action_id, _ in results}) == 1
    with Session(database.engine) as database_session:
        assert database_session.scalar(select(func.count()).select_from(HealthAction)) == 1
