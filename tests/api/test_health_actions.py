from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from conftest import DATABASE_URL, ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.application import services
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import Account, HealthAction, Person, SessionRecord
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _create_action(client: TestClient, person_id: str, **overrides: object):
    payload = {
        "title": "Take an evening walk",
        "description": "Walk for twenty minutes",
        "due_at": "2026-08-01T18:30:00+08:00",
    }
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/actions",
        headers=csrf_headers(client),
        json=payload,
    )


def test_create_returns_only_server_contract_fields_and_normalizes_input(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    response = _create_action(
        client,
        person_id,
        title="  Take an evening walk  ",
        description="  Walk for twenty minutes  ",
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "id",
        "person_id",
        "title",
        "description",
        "due_at",
        "status",
        "completed_at",
        "created_at",
        "updated_at",
    }
    assert uuid.UUID(body["id"])
    assert body["person_id"] == person_id
    assert body["title"] == "Take an evening walk"
    assert body["description"] == "Walk for twenty minutes"
    assert body["due_at"] == "2026-08-01T10:30:00Z"
    assert body["status"] == "todo"
    assert body["completed_at"] is None
    assert datetime.fromisoformat(body["created_at"])
    assert datetime.fromisoformat(body["updated_at"])


def test_blank_description_becomes_null(client: TestClient) -> None:
    assert register(client).status_code == 201
    response = _create_action(client, _person_id(client), description="   ")
    assert response.status_code == 201
    assert response.json()["description"] is None


@pytest.mark.parametrize(
    ("title", "expected_status"),
    [
        ("x", 201),
        ("x" * 240, 201),
        ("   ", 422),
        ("x" * 241, 422),
    ],
)
def test_title_validation(client: TestClient, title: str, expected_status: int) -> None:
    assert register(client).status_code == 201
    assert _create_action(client, _person_id(client), title=title).status_code == expected_status


def test_due_at_requires_timezone_and_normalizes_to_utc(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    missing_timezone = _create_action(
        client,
        person_id,
        due_at="2026-08-01T18:30:00",
    )
    normalized = _create_action(
        client,
        person_id,
        due_at="2026-08-01T18:30:00-04:00",
    )

    assert missing_timezone.status_code == 422
    assert normalized.status_code == 201
    assert normalized.json()["due_at"] == "2026-08-01T22:30:00Z"


def test_unknown_and_server_owned_create_fields_are_rejected(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    for field, value in [
        ("unknown", "value"),
        ("status", "done"),
        ("completed_at", datetime.now(UTC).isoformat()),
        ("id", str(uuid.uuid4())),
        ("created_at", datetime.now(UTC).isoformat()),
        ("updated_at", datetime.now(UTC).isoformat()),
    ]:
        assert _create_action(client, person_id, **{field: value}).status_code == 422


def test_list_is_newest_first_with_deterministic_id_tie_breaker(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    first = _create_action(client, person_id, title="First")
    second = _create_action(client, person_id, title="Second")
    assert first.status_code == second.status_code == 201

    action_ids = [uuid.UUID(first.json()["id"]), uuid.UUID(second.json()["id"])]
    database = Database(DATABASE_URL)
    with Session(database.engine) as database_session:
        database_session.execute(
            update(HealthAction)
            .where(HealthAction.id.in_(action_ids))
            .values(created_at=datetime(2026, 7, 29, 12, 0, tzinfo=UTC))
        )
        database_session.commit()

    listing = client.get(f"/v1/persons/{person_id}/actions")
    expected = [str(action_id) for action_id in sorted(action_ids, reverse=True)]
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()] == expected


def test_single_action_retrieval(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    created = _create_action(client, person_id)
    assert created.status_code == 201

    response = client.get(f"/v1/persons/{person_id}/actions/{created.json()['id']}")
    assert response.status_code == 200
    assert response.json() == created.json()


def test_action_endpoints_require_authentication(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    created = _create_action(client, person_id)
    action_id = created.json()["id"]
    client.cookies.clear()

    assert _create_action(client, person_id).status_code == 401
    assert client.get(f"/v1/persons/{person_id}/actions").status_code == 401
    assert client.get(f"/v1/persons/{person_id}/actions/{action_id}").status_code == 401
    assert (
        client.post(
            f"/v1/persons/{person_id}/actions/{action_id}/complete",
            headers={"Origin": ORIGIN},
        ).status_code
        == 401
    )


@pytest.mark.parametrize("path_kind", ["create", "complete"])
def test_action_commands_require_valid_origin_and_csrf(
    client: TestClient,
    path_kind: str,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    created = _create_action(client, person_id)
    action_id = created.json()["id"]
    path = (
        f"/v1/persons/{person_id}/actions"
        if path_kind == "create"
        else f"/v1/persons/{person_id}/actions/{action_id}/complete"
    )
    payload = {"title": "New action"} if path_kind == "create" else None

    missing_csrf = client.post(path, headers={"Origin": ORIGIN}, json=payload)
    invalid_csrf = client.post(
        path,
        headers={"Origin": ORIGIN, "X-CSRF-Token": "invalid"},
        json=payload,
    )
    invalid_origin = client.post(
        path,
        headers=csrf_headers(client, origin="https://attacker.example"),
        json=payload,
    )
    assert missing_csrf.status_code == invalid_csrf.status_code == 403
    assert invalid_origin.status_code == 403


def test_person_and_action_ownership_return_generic_404s(client: TestClient) -> None:
    assert register(client, email="owner-a@example.com").status_code == 201
    person_a = _person_id(client)
    action_a = _create_action(client, person_a)
    assert action_a.status_code == 201
    action_a_id = action_a.json()["id"]

    person_b_response = client.post(
        "/v1/persons",
        headers=csrf_headers(client),
        json={"display_name": "Second Person", "relationship": "family"},
    )
    assert person_b_response.status_code == 201
    person_b = person_b_response.json()["id"]
    cross_person = client.get(f"/v1/persons/{person_b}/actions/{action_a_id}")
    assert cross_person.status_code == 404
    assert cross_person.json() == {"detail": "Action not found"}

    other = TestClient(client.app, base_url=ORIGIN)
    assert register(other, email="owner-b@example.com").status_code == 201
    other_person = _person_id(other)
    other_action = _create_action(other, other_person, title="Other owner action")
    assert other_action.status_code == 201
    other_action_id = other_action.json()["id"]

    foreign_person_responses = [
        _create_action(other, person_a),
        other.get(f"/v1/persons/{person_a}/actions"),
        other.get(f"/v1/persons/{person_a}/actions/{action_a_id}"),
        other.post(
            f"/v1/persons/{person_a}/actions/{action_a_id}/complete",
            headers=csrf_headers(other),
        ),
    ]
    assert all(response.status_code == 404 for response in foreign_person_responses)
    assert all(
        response.json() == {"detail": "Person not found"} for response in foreign_person_responses
    )

    foreign_action = other.get(f"/v1/persons/{other_person}/actions/{action_a_id}")
    complete_foreign_action = other.post(
        f"/v1/persons/{other_person}/actions/{action_a_id}/complete",
        headers=csrf_headers(other),
    )
    assert foreign_action.status_code == complete_foreign_action.status_code == 404
    assert foreign_action.json() == complete_foreign_action.json() == {"detail": "Action not found"}
    assert other_action_id != action_a_id


def test_missing_person_and_action_return_generic_404s(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    missing_person = str(uuid.uuid4())
    missing_action = str(uuid.uuid4())

    assert _create_action(client, missing_person).json() == {"detail": "Person not found"}
    assert client.get(f"/v1/persons/{missing_person}/actions").json() == {
        "detail": "Person not found"
    }
    assert client.get(f"/v1/persons/{person_id}/actions/{missing_action}").json() == {
        "detail": "Action not found"
    }
    assert client.post(
        f"/v1/persons/{person_id}/actions/{missing_action}/complete",
        headers=csrf_headers(client),
    ).json() == {"detail": "Action not found"}


def test_deleting_person_cascades_health_actions(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = uuid.UUID(_person_id(client))
    assert _create_action(client, str(person_id)).status_code == 201
    database = Database(DATABASE_URL)

    with Session(database.engine) as database_session:
        person = database_session.get(Person, person_id)
        assert person is not None
        database_session.delete(person)
        database_session.commit()
        assert database_session.scalar(select(func.count()).select_from(HealthAction)) == 0


def test_repeated_gets_produce_zero_database_writes(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    created = _create_action(client, person_id)
    action_id = created.json()["id"]
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
            )

    before = snapshot()
    for _ in range(3):
        assert client.get(f"/v1/persons/{person_id}/actions").status_code == 200
        assert client.get(f"/v1/persons/{person_id}/actions/{action_id}").status_code == 200
    assert snapshot() == before


def test_completion_is_terminal_idempotent_and_uses_one_instant(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    created = _create_action(client, person_id)
    action_id = created.json()["id"]
    path = f"/v1/persons/{person_id}/actions/{action_id}/complete"

    first = client.post(path, headers=csrf_headers(client))
    second = client.post(path, headers=csrf_headers(client))

    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "done"
    assert first.json()["completed_at"] is not None
    assert first.json()["completed_at"] == first.json()["updated_at"]
    assert second.json()["completed_at"] == first.json()["completed_at"]
    assert second.json()["updated_at"] == first.json()["updated_at"]


def test_concurrent_completion_produces_one_stable_instant(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = uuid.UUID(_person_id(client))
    created = _create_action(client, str(person_id))
    action_id = uuid.UUID(created.json()["id"])
    database = Database(DATABASE_URL)
    with Session(database.engine) as database_session:
        owner_account_id = database_session.scalar(select(Account.id))
    assert owner_account_id is not None

    def complete() -> tuple[str, datetime | None, datetime]:
        with Session(database.engine) as database_session:
            action = services.complete_health_action(
                database_session,
                owner_account_id=owner_account_id,
                person_id=person_id,
                action_id=action_id,
            )
            assert action is not None
            return action.status, action.completed_at, action.updated_at

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: complete(), range(2)))

    assert {status for status, _, _ in results} == {"done"}
    assert len({completed_at for _, completed_at, _ in results}) == 1
    assert all(completed_at == updated_at for _, completed_at, updated_at in results)


def test_integrity_error_is_rolled_back_and_mapped_to_generic_422(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    def raise_integrity_error(*_args: object, **_kwargs: object) -> HealthAction:
        raise services.HealthActionIntegrityError

    monkeypatch.setattr(services, "create_health_action", raise_integrity_error)
    response = _create_action(client, person_id)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert "constraint" not in response.text.casefold()
    assert "sql" not in response.text.casefold()


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_unsupported_mutation_methods_return_405(
    client: TestClient,
    method: str,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    created = _create_action(client, person_id)
    action_id = created.json()["id"]
    paths = [
        f"/v1/persons/{person_id}/actions",
        f"/v1/persons/{person_id}/actions/{action_id}",
        f"/v1/persons/{person_id}/actions/{action_id}/complete",
    ]

    for path in paths:
        response = client.request(method, path, headers=csrf_headers(client), json={})
        assert response.status_code == 405
