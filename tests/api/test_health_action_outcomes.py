from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import DATABASE_URL, ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.application import services
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import (
    Account,
    HealthAction,
    HealthActionOutcome,
    Person,
    SessionRecord,
)
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _create_action(client: TestClient, person_id: str, *, title: str = "Evening walk"):
    return client.post(
        f"/v1/persons/{person_id}/actions",
        headers=csrf_headers(client),
        json={"title": title},
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


def _create_outcome(
    client: TestClient,
    person_id: str,
    action_id: str,
    *,
    note: str = "I slept more soundly after the walk.",
    observed_at: str | None = None,
    **overrides: object,
):
    payload: dict[str, object] = {
        "note": note,
        "observed_at": observed_at or (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    }
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/actions/{action_id}/outcomes",
        headers=csrf_headers(client),
        json=payload,
    )


def test_create_explicit_outcome_returns_only_contract_fields_and_normalizes_input(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_done_action(client, person_id)

    response = _create_outcome(
        client,
        person_id,
        action_id,
        note="  I slept more soundly after the walk.  ",
        observed_at="2026-07-29T20:30:00+08:00",
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "action_id", "note", "observed_at", "created_at"}
    assert uuid.UUID(body["id"])
    assert body["action_id"] == action_id
    assert body["note"] == "I slept more soundly after the walk."
    assert body["observed_at"] == "2026-07-29T12:30:00Z"
    assert datetime.fromisoformat(body["created_at"])
    assert set(HealthActionOutcome.__table__.columns.keys()) == {
        "id",
        "action_id",
        "note",
        "observed_at",
        "created_at",
    }


def test_outcomes_require_done_action_and_completion_never_creates_one(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    created = _create_action(client, person_id)
    assert created.status_code == 201
    action_id = created.json()["id"]

    before_completion = _create_outcome(client, person_id, action_id)
    assert before_completion.status_code == 422
    assert before_completion.json() == {"detail": "Invalid request"}

    completed = _complete_action(client, person_id, action_id)
    assert completed.status_code == 200
    listing = client.get(f"/v1/persons/{person_id}/actions/{action_id}/outcomes")
    assert listing.status_code == 200
    assert listing.json() == []


@pytest.mark.parametrize(
    ("note", "expected_status"),
    [
        ("x", 201),
        ("  " + ("x" * 2000) + "  ", 201),
        ("   ", 422),
        ("x" * 2001, 422),
    ],
)
def test_note_is_required_trimmed_and_bounded(
    client: TestClient,
    note: str,
    expected_status: int,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_done_action(client, person_id)

    response = _create_outcome(client, person_id, action_id, note=note)

    assert response.status_code == expected_status
    if expected_status == 201:
        assert response.json()["note"] == note.strip()


def test_observed_at_requires_timezone_normalizes_to_utc_and_rejects_future_skew(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_done_action(client, person_id)

    missing_timezone = _create_outcome(
        client,
        person_id,
        action_id,
        observed_at="2026-07-29T20:30:00",
    )
    normalized = _create_outcome(
        client,
        person_id,
        action_id,
        observed_at="2026-07-29T20:30:00-04:00",
    )
    too_far_future = _create_outcome(
        client,
        person_id,
        action_id,
        observed_at=(datetime.now(UTC) + timedelta(minutes=6)).isoformat(),
    )

    assert missing_timezone.status_code == 422
    assert normalized.status_code == 201
    assert normalized.json()["observed_at"] == "2026-07-30T00:30:00Z"
    assert too_far_future.status_code == 422


def test_unknown_path_and_server_owned_fields_are_rejected(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_done_action(client, person_id)

    for field, value in [
        ("unknown", "value"),
        ("id", str(uuid.uuid4())),
        ("action_id", str(uuid.uuid4())),
        ("person_id", person_id),
        ("created_at", datetime.now(UTC).isoformat()),
    ]:
        payload: dict[str, object] = {
            "note": "Observation",
            "observed_at": datetime.now(UTC).isoformat(),
            field: value,
        }
        response = client.post(
            f"/v1/persons/{person_id}/actions/{action_id}/outcomes",
            headers=csrf_headers(client),
            json=payload,
        )
        assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"observed_at": "2026-07-29T12:30:00Z"},
        {"note": "Observation"},
    ],
)
def test_note_and_observed_at_are_required(
    client: TestClient,
    payload: dict[str, object],
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_done_action(client, person_id)

    response = client.post(
        f"/v1/persons/{person_id}/actions/{action_id}/outcomes",
        headers=csrf_headers(client),
        json=payload,
    )

    assert response.status_code == 422


def test_one_action_accepts_multiple_outcomes_and_lists_deterministically(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_done_action(client, person_id)
    first = _create_outcome(client, person_id, action_id, note="First observation")
    second = _create_outcome(client, person_id, action_id, note="Second observation")
    third = _create_outcome(client, person_id, action_id, note="Third observation")
    fourth = _create_outcome(client, person_id, action_id, note="Fourth observation")
    assert first.status_code == second.status_code == third.status_code == fourth.status_code == 201

    first_id = uuid.UUID(first.json()["id"])
    second_id = uuid.UUID(second.json()["id"])
    third_id = uuid.UUID(third.json()["id"])
    fourth_id = uuid.UUID(fourth.json()["id"])
    database = Database(DATABASE_URL)
    with Session(database.engine) as database_session:
        database_session.execute(
            update(HealthActionOutcome)
            .where(HealthActionOutcome.id == first_id)
            .values(
                observed_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
                created_at=datetime(2026, 7, 29, 15, 0, tzinfo=UTC),
            )
        )
        database_session.execute(
            update(HealthActionOutcome)
            .where(HealthActionOutcome.id == second_id)
            .values(
                observed_at=datetime(2026, 7, 29, 13, 0, tzinfo=UTC),
                created_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
            )
        )
        tied_ids = [third_id, fourth_id]
        database_session.execute(
            update(HealthActionOutcome)
            .where(HealthActionOutcome.id.in_(tied_ids))
            .values(
                observed_at=datetime(2026, 7, 29, 13, 0, tzinfo=UTC),
                created_at=datetime(2026, 7, 29, 14, 0, tzinfo=UTC),
            )
        )
        database_session.commit()

    listing = client.get(f"/v1/persons/{person_id}/actions/{action_id}/outcomes")
    expected = [
        *(str(outcome_id) for outcome_id in sorted(tied_ids, reverse=True)),
        str(second_id),
        str(first_id),
    ]
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()] == expected


def test_single_outcome_retrieval_is_scoped_to_its_action(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_done_action(client, person_id, title="First action")
    other_action_id = _create_done_action(client, person_id, title="Second action")
    created = _create_outcome(client, person_id, action_id)
    assert created.status_code == 201
    outcome_id = created.json()["id"]

    response = client.get(f"/v1/persons/{person_id}/actions/{action_id}/outcomes/{outcome_id}")
    wrong_action = client.get(
        f"/v1/persons/{person_id}/actions/{other_action_id}/outcomes/{outcome_id}"
    )

    assert response.status_code == 200
    assert response.json() == created.json()
    assert wrong_action.status_code == 404
    assert wrong_action.json() == {"detail": "Outcome not found"}


def test_outcome_endpoints_require_authentication_and_commands_require_origin_csrf(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_done_action(client, person_id)
    created = _create_outcome(client, person_id, action_id)
    outcome_id = created.json()["id"]

    missing_csrf = client.post(
        f"/v1/persons/{person_id}/actions/{action_id}/outcomes",
        headers={"Origin": ORIGIN},
        json={
            "note": "Observation",
            "observed_at": datetime.now(UTC).isoformat(),
        },
    )
    invalid_origin = client.post(
        f"/v1/persons/{person_id}/actions/{action_id}/outcomes",
        headers=csrf_headers(client, origin="https://attacker.example"),
        json={
            "note": "Observation",
            "observed_at": datetime.now(UTC).isoformat(),
        },
    )
    assert missing_csrf.status_code == invalid_origin.status_code == 403

    client.cookies.clear()
    assert _create_outcome(client, person_id, action_id).status_code == 401
    assert client.get(f"/v1/persons/{person_id}/actions/{action_id}/outcomes").status_code == 401
    assert (
        client.get(f"/v1/persons/{person_id}/actions/{action_id}/outcomes/{outcome_id}").status_code
        == 401
    )


def test_outcome_ownership_chain_returns_generic_404s(client: TestClient) -> None:
    assert register(client, email="owner-a@example.com").status_code == 201
    person_a = _person_id(client)
    action_a = _create_done_action(client, person_a)
    outcome_a = _create_outcome(client, person_a, action_a)
    assert outcome_a.status_code == 201
    outcome_a_id = outcome_a.json()["id"]

    person_b_response = client.post(
        "/v1/persons",
        headers=csrf_headers(client),
        json={"display_name": "Second Person", "relationship": "family"},
    )
    assert person_b_response.status_code == 201
    person_b = person_b_response.json()["id"]
    cross_person = client.get(f"/v1/persons/{person_b}/actions/{action_a}/outcomes")
    assert cross_person.status_code == 404
    assert cross_person.json() == {"detail": "Action not found"}

    other = TestClient(client.app, base_url=ORIGIN)
    assert register(other, email="owner-b@example.com").status_code == 201
    other_person = _person_id(other)
    other_action = _create_done_action(other, other_person, title="Other owner action")

    foreign_person_responses = [
        _create_outcome(other, person_a, action_a),
        other.get(f"/v1/persons/{person_a}/actions/{action_a}/outcomes"),
        other.get(f"/v1/persons/{person_a}/actions/{action_a}/outcomes/{outcome_a_id}"),
    ]
    assert all(response.status_code == 404 for response in foreign_person_responses)
    assert all(
        response.json() == {"detail": "Person not found"} for response in foreign_person_responses
    )

    foreign_action_responses = [
        _create_outcome(other, other_person, action_a),
        other.get(f"/v1/persons/{other_person}/actions/{action_a}/outcomes"),
        other.get(f"/v1/persons/{other_person}/actions/{action_a}/outcomes/{outcome_a_id}"),
    ]
    assert all(response.status_code == 404 for response in foreign_action_responses)
    assert all(
        response.json() == {"detail": "Action not found"} for response in foreign_action_responses
    )
    assert other_action != action_a


def test_missing_action_and_outcome_return_generic_404s(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_done_action(client, person_id)
    missing_action = str(uuid.uuid4())
    missing_outcome = str(uuid.uuid4())

    assert _create_outcome(client, person_id, missing_action).json() == {
        "detail": "Action not found"
    }
    assert client.get(f"/v1/persons/{person_id}/actions/{missing_action}/outcomes").json() == {
        "detail": "Action not found"
    }
    assert client.get(
        f"/v1/persons/{person_id}/actions/{action_id}/outcomes/{missing_outcome}"
    ).json() == {"detail": "Outcome not found"}


def test_repeated_gets_produce_zero_database_writes(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_done_action(client, person_id)
    created = _create_outcome(client, person_id, action_id)
    assert created.status_code == 201
    outcome_id = created.json()["id"]
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
                        select(
                            HealthActionOutcome.id,
                            HealthActionOutcome.note,
                            HealthActionOutcome.observed_at,
                            HealthActionOutcome.created_at,
                        ).order_by(HealthActionOutcome.id)
                    ).tuples()
                ),
            )

    before = snapshot()
    for _ in range(3):
        assert (
            client.get(f"/v1/persons/{person_id}/actions/{action_id}/outcomes").status_code == 200
        )
        assert (
            client.get(
                f"/v1/persons/{person_id}/actions/{action_id}/outcomes/{outcome_id}"
            ).status_code
            == 200
        )
    assert snapshot() == before


def test_integrity_error_is_rolled_back_and_mapped_to_generic_422(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_done_action(client, person_id)

    def create_invalid_outcome(
        database_session: Session,
        resolved_action_id: uuid.UUID,
        *,
        note: str,
        observed_at: datetime,
    ) -> HealthActionOutcome:
        assert note
        outcome = HealthActionOutcome(
            action_id=resolved_action_id,
            note="",
            observed_at=observed_at,
        )
        database_session.add(outcome)
        return outcome

    monkeypatch.setattr(
        services.HealthActionOutcomeRepository,
        "create_for_action",
        create_invalid_outcome,
    )
    response = _create_outcome(client, person_id, action_id)

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    database = Database(DATABASE_URL)
    with Session(database.engine) as database_session:
        assert database_session.scalar(select(func.count()).select_from(HealthActionOutcome)) == 0


def test_deleting_person_cascades_outcomes_through_action(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = uuid.UUID(_person_id(client))
    action_id = _create_done_action(client, str(person_id))
    assert _create_outcome(client, str(person_id), action_id).status_code == 201
    database = Database(DATABASE_URL)

    with Session(database.engine) as database_session:
        person = database_session.get(Person, person_id)
        assert person is not None
        database_session.delete(person)
        database_session.commit()
        assert database_session.scalar(select(func.count()).select_from(HealthAction)) == 0
        assert database_session.scalar(select(func.count()).select_from(HealthActionOutcome)) == 0


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_outcomes_have_no_update_or_delete_operation(
    client: TestClient,
    method: str,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_done_action(client, person_id)
    created = _create_outcome(client, person_id, action_id)
    assert created.status_code == 201
    outcome_id = created.json()["id"]
    paths = [
        f"/v1/persons/{person_id}/actions/{action_id}/outcomes",
        f"/v1/persons/{person_id}/actions/{action_id}/outcomes/{outcome_id}",
    ]

    for path in paths:
        response = client.request(method, path, headers=csrf_headers(client), json={})
        assert response.status_code == 405
