from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from conftest import DATABASE_URL, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import (
    Account,
    HealthAction,
    HealthActionOutcome,
    HealthMetric,
    Person,
    SessionRecord,
    SymptomLog,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _today_url(person_id: str) -> str:
    return f"/v1/persons/{person_id}/assistant/today"


def _create_metric(client: TestClient, person_id: str, **overrides: object):
    payload: dict[str, object] = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "heart_rate_bpm": 72,
    }
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json=payload,
    )


def _create_symptom(client: TestClient, person_id: str, **overrides: object):
    payload: dict[str, object] = {
        "symptom": "Headache",
        "occurred_at": datetime.now(UTC).isoformat(),
        "severity": 2,
    }
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/symptoms",
        headers=csrf_headers(client),
        json=payload,
    )


def _create_action(client: TestClient, person_id: str, **overrides: object):
    payload: dict[str, object] = {"title": "Evening walk"}
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/actions",
        headers=csrf_headers(client),
        json=payload,
    )


def _complete_action(client: TestClient, person_id: str, action_id: str):
    return client.post(
        f"/v1/persons/{person_id}/actions/{action_id}/complete",
        headers=csrf_headers(client),
    )


def _create_done_action(client: TestClient, person_id: str, *, title: str = "Evening walk") -> str:
    created = _create_action(client, person_id, title=title)
    assert created.status_code == 201
    action_id = created.json()["id"]
    assert _complete_action(client, person_id, action_id).status_code == 200
    return action_id


def _create_outcome(client: TestClient, person_id: str, action_id: str, **overrides: object):
    payload: dict[str, object] = {
        "note": "Slept better after the walk.",
        "observed_at": datetime.now(UTC).isoformat(),
    }
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/actions/{action_id}/outcomes",
        headers=csrf_headers(client),
        json=payload,
    )


def test_missing_person_returns_generic_404(client: TestClient) -> None:
    assert register(client).status_code == 201
    response = client.get(_today_url(str(uuid.uuid4())))
    assert response.status_code == 404
    assert response.json() == {"detail": "Person not found"}


def test_foreign_person_returns_generic_404_without_leaking_data(client: TestClient) -> None:
    assert register(client, email="owner-a@example.com").status_code == 201
    person_a = _person_id(client)

    other = TestClient(client.app, base_url="http://127.0.0.1:3000")
    assert register(other, email="owner-b@example.com").status_code == 201

    response = other.get(_today_url(person_a))
    assert response.status_code == 404
    assert response.json() == {"detail": "Person not found"}
    assert person_a not in response.text


def test_endpoint_requires_authentication(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    client.cookies.clear()
    response = client.get(_today_url(person_id))
    assert response.status_code == 401


def test_no_records_yields_insufficient_data_guidance_and_empty_lists(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    response = client.get(_today_url(person_id))

    assert response.status_code == 200
    body = response.json()
    assert body["latest_metric"] is None
    assert body["recent_symptoms"] == []
    assert body["open_or_recent_actions"] == []
    assert body["recent_outcomes"] == []
    assert body["insights"] == []
    assert len(body["daily_attention"]) == 1
    item = body["daily_attention"][0]
    assert item["kind"] == "insufficient_data"
    assert item["confidence"] == "low"
    assert item["evidence_ids"] == []
    assert item["rule_version"]


def test_aggregates_metrics_symptoms_actions_and_outcomes_with_resolvable_evidence(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    metric = _create_metric(client, person_id)
    assert metric.status_code == 201
    symptom = _create_symptom(client, person_id)
    assert symptom.status_code == 201
    action_id = _create_done_action(client, person_id)
    outcome = _create_outcome(client, person_id, action_id)
    assert outcome.status_code == 201
    open_action = _create_action(client, person_id, title="Track blood pressure")
    assert open_action.status_code == 201

    response = client.get(_today_url(person_id))
    assert response.status_code == 200
    body = response.json()

    assert body["lookback_days"] == 14
    assert datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))
    assert body["latest_metric"]["id"] == metric.json()["id"]
    assert [row["id"] for row in body["recent_symptoms"]] == [symptom.json()["id"]]
    action_ids = {row["id"] for row in body["open_or_recent_actions"]}
    assert action_ids == {action_id, open_action.json()["id"]}
    assert [row["id"] for row in body["recent_outcomes"]] == [outcome.json()["id"]]

    known_symptom_ids = {row["id"] for row in body["recent_symptoms"]}
    known_action_ids = action_ids
    known_outcome_ids = {row["id"] for row in body["recent_outcomes"]}
    known_metric_ids = {body["latest_metric"]["id"]}
    all_known_ids = known_symptom_ids | known_action_ids | known_outcome_ids | known_metric_ids

    kinds = {item["kind"] for item in body["daily_attention"]}
    assert "symptom_recently_reported" in kinds
    assert "action_open_or_due" in kinds
    assert "outcome_recorded" in kinds
    assert "no_recent_metric" not in kinds
    for item in body["daily_attention"]:
        for evidence_id in item["evidence_ids"]:
            assert evidence_id in all_known_ids


def test_no_recent_metric_flagged_when_only_stale_metric_exists(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = uuid.UUID(_person_id(client))
    metric = _create_metric(client, str(person_id))
    assert metric.status_code == 201
    metric_id = uuid.UUID(metric.json()["id"])

    database = Database(DATABASE_URL)
    with Session(database.engine) as database_session:
        database_session.execute(select(HealthMetric).where(HealthMetric.id == metric_id))
        stale = database_session.get(HealthMetric, metric_id)
        assert stale is not None
        stale.recorded_at = datetime.now(UTC) - timedelta(days=30)
        database_session.commit()

    response = client.get(_today_url(str(person_id)))
    assert response.status_code == 200
    body = response.json()
    assert body["latest_metric"]["id"] == str(metric_id)
    kinds = {item["kind"] for item in body["daily_attention"]}
    assert "no_recent_metric" in kinds


def test_completed_action_outside_lookback_is_excluded_but_open_actions_remain(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = uuid.UUID(_person_id(client))
    action_id = uuid.UUID(_create_done_action(client, str(person_id)))
    still_open = _create_action(client, str(person_id), title="Still open")
    assert still_open.status_code == 201

    database = Database(DATABASE_URL)
    with Session(database.engine) as database_session:
        completed = database_session.get(HealthAction, action_id)
        assert completed is not None
        completed.completed_at = datetime.now(UTC) - timedelta(days=30)
        database_session.commit()

    response = client.get(_today_url(str(person_id)))
    assert response.status_code == 200
    body = response.json()
    action_ids = {row["id"] for row in body["open_or_recent_actions"]}
    assert action_ids == {still_open.json()["id"]}


def test_ordering_is_deterministic_and_newest_first(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = uuid.UUID(_person_id(client))
    first = _create_symptom(client, str(person_id), symptom="First")
    second = _create_symptom(client, str(person_id), symptom="Second")
    third = _create_symptom(client, str(person_id), symptom="Third")
    assert first.status_code == second.status_code == third.status_code == 201
    first_id = uuid.UUID(first.json()["id"])
    second_id = uuid.UUID(second.json()["id"])
    third_id = uuid.UUID(third.json()["id"])

    database = Database(DATABASE_URL)
    with Session(database.engine) as database_session:
        for symptom_id, occurred_at in (
            (first_id, datetime(2026, 7, 29, 12, 0, tzinfo=UTC)),
            (second_id, datetime(2026, 7, 29, 13, 0, tzinfo=UTC)),
            (third_id, datetime(2026, 7, 29, 13, 0, tzinfo=UTC)),
        ):
            row = database_session.get(SymptomLog, symptom_id)
            assert row is not None
            row.occurred_at = occurred_at
        database_session.commit()

    expected = [str(third_id), str(second_id), str(first_id)]

    first_call = client.get(_today_url(str(person_id))).json()
    second_call = client.get(_today_url(str(person_id))).json()
    assert [row["id"] for row in first_call["recent_symptoms"]] == expected
    assert [row["id"] for row in second_call["recent_symptoms"]] == expected


def test_repeated_gets_are_stable_and_produce_zero_database_writes(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    assert _create_metric(client, person_id).status_code == 201
    assert _create_symptom(client, person_id).status_code == 201
    action_id = _create_done_action(client, person_id)
    assert _create_outcome(client, person_id, action_id).status_code == 201

    database = Database(DATABASE_URL)

    def snapshot() -> tuple[list[tuple[object, ...]], ...]:
        with Session(database.engine) as database_session:
            return (
                list(
                    database_session.execute(
                        select(Account.id, Account.updated_at).order_by(Account.id)
                    ).tuples()
                ),
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
                        select(HealthMetric.id, HealthMetric.created_at).order_by(HealthMetric.id)
                    ).tuples()
                ),
                list(
                    database_session.execute(
                        select(SymptomLog.id, SymptomLog.created_at).order_by(SymptomLog.id)
                    ).tuples()
                ),
                list(
                    database_session.execute(
                        select(
                            HealthAction.id,
                            HealthAction.status,
                            HealthAction.completed_at,
                            HealthAction.updated_at,
                        ).order_by(HealthAction.id)
                    ).tuples()
                ),
                list(
                    database_session.execute(
                        select(HealthActionOutcome.id, HealthActionOutcome.created_at).order_by(
                            HealthActionOutcome.id
                        )
                    ).tuples()
                ),
            )

    before = snapshot()
    responses = []
    for _ in range(3):
        response = client.get(_today_url(person_id))
        assert response.status_code == 200
        responses.append(response.json())
    assert snapshot() == before

    for body in responses:
        body.pop("generated_at")
    assert responses[0] == responses[1] == responses[2]
