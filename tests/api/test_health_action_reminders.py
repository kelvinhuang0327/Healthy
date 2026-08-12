from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta

import pytest
from conftest import DATABASE_URL, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.application import services
from healthy.domain import reminders as reminders_domain
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import Account, HealthAction, HealthActionReminder
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _create_action(client: TestClient, person_id: str, *, title: str = "Take a walk") -> str:
    response = client.post(
        f"/v1/persons/{person_id}/actions",
        headers=csrf_headers(client),
        json={"title": title},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _reminder_url(person_id: str, action_id: str) -> str:
    return f"/v1/persons/{person_id}/actions/{action_id}/reminder"


def _due_url(person_id: str) -> str:
    return f"/v1/persons/{person_id}/reminders/due"


def _put_reminder(
    client: TestClient,
    person_id: str,
    action_id: str,
    *,
    timezone_name: str = "UTC",
    local_time: str = "00:00",
):
    return client.put(
        _reminder_url(person_id, action_id),
        headers=csrf_headers(client),
        json={"timezone_name": timezone_name, "local_time": local_time},
    )


def test_domain_due_evaluation_is_deterministic_and_timezone_aware() -> None:
    before = reminders_domain.evaluate_due(
        action_status="todo",
        timezone_name="Asia/Taipei",
        local_time=time(18),
        now=datetime(2026, 8, 12, 9, 59, tzinfo=UTC),
        snoozed_until=None,
        last_acknowledged_local_date=None,
    )
    after = reminders_domain.evaluate_due(
        action_status="todo",
        timezone_name="Asia/Taipei",
        local_time=time(18),
        now=datetime(2026, 8, 12, 10, tzinfo=UTC),
        snoozed_until=None,
        last_acknowledged_local_date=None,
    )

    assert before == reminders_domain.ReminderDueState(False, date(2026, 8, 12))
    assert after == reminders_domain.ReminderDueState(True, date(2026, 8, 12))


def test_domain_acknowledgement_snooze_completion_and_invalid_timezone() -> None:
    now = datetime(2026, 8, 12, 10, tzinfo=UTC)
    local_date = date(2026, 8, 12)
    acknowledged = reminders_domain.evaluate_due(
        action_status="todo",
        timezone_name="UTC",
        local_time=time(9),
        now=now,
        snoozed_until=None,
        last_acknowledged_local_date=local_date,
    )
    snoozed = reminders_domain.evaluate_due(
        action_status="todo",
        timezone_name="UTC",
        local_time=time(9),
        now=now,
        snoozed_until=now + timedelta(minutes=1),
        last_acknowledged_local_date=None,
    )
    expired = reminders_domain.evaluate_due(
        action_status="todo",
        timezone_name="UTC",
        local_time=time(9),
        now=now + timedelta(minutes=1),
        snoozed_until=now + timedelta(minutes=1),
        last_acknowledged_local_date=None,
    )
    completed = reminders_domain.evaluate_due(
        action_status="done",
        timezone_name="UTC",
        local_time=time(9),
        now=now,
        snoozed_until=None,
        last_acknowledged_local_date=None,
    )

    assert not acknowledged.is_due
    assert not snoozed.is_due
    assert expired.is_due
    assert not completed.is_due
    with pytest.raises(reminders_domain.InvalidTimezoneError):
        reminders_domain.validate_timezone("+08:00")


def test_domain_dst_behavior_is_wall_clock_based() -> None:
    spring_forward = reminders_domain.evaluate_due(
        action_status="todo",
        timezone_name="America/New_York",
        local_time=time(2, 30),
        now=datetime(2026, 3, 8, 7, 30, tzinfo=UTC),
        snoozed_until=None,
        last_acknowledged_local_date=None,
    )
    fall_back_before_ack = reminders_domain.evaluate_due(
        action_status="todo",
        timezone_name="America/New_York",
        local_time=time(1, 30),
        now=datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
        snoozed_until=None,
        last_acknowledged_local_date=None,
    )
    fall_back_after_ack = reminders_domain.evaluate_due(
        action_status="todo",
        timezone_name="America/New_York",
        local_time=time(1, 30),
        now=datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
        snoozed_until=None,
        last_acknowledged_local_date=date(2026, 11, 1),
    )

    assert spring_forward.is_due
    assert fall_back_before_ack.is_due
    assert not fall_back_after_ack.is_due
    assert fall_back_before_ack.local_date == fall_back_after_ack.local_date == date(2026, 11, 1)


def test_explicit_schedule_crud_is_idempotent_and_preserves_one_row(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_action(client, person_id)

    first = _put_reminder(client, person_id, action_id)
    retry = _put_reminder(client, person_id, action_id)
    updated = _put_reminder(
        client,
        person_id,
        action_id,
        timezone_name="America/New_York",
        local_time="09:30",
    )

    assert first.status_code == retry.status_code == updated.status_code == 200
    assert first.json()["id"] == retry.json()["id"] == updated.json()["id"]
    assert updated.json()["timezone_name"] == "America/New_York"
    assert updated.json()["local_time"] == "09:30:00"

    with Session(Database(DATABASE_URL).engine) as database_session:
        assert (
            database_session.scalar(
                select(func.count())
                .select_from(HealthActionReminder)
                .where(HealthActionReminder.action_id == uuid.UUID(action_id))
            )
            == 1
        )

    fetched = client.get(_reminder_url(person_id, action_id))
    assert fetched.status_code == 200
    assert fetched.json() == updated.json()

    assert (
        _put_reminder(
            client,
            person_id,
            action_id,
            timezone_name="+08:00",
        ).status_code
        == 422
    )
    assert (
        _put_reminder(
            client,
            person_id,
            action_id,
            local_time="09:00:00+08:00",
        ).status_code
        == 422
    )

    deleted = client.delete(_reminder_url(person_id, action_id), headers=csrf_headers(client))
    repeated_delete = client.delete(
        _reminder_url(person_id, action_id),
        headers=csrf_headers(client),
    )
    assert deleted.status_code == repeated_delete.status_code == 204
    assert client.get(_reminder_url(person_id, action_id)).status_code == 404


def test_done_actions_reject_schedule_changes_but_keep_existing_schedule(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_action(client, person_id)
    assert _put_reminder(client, person_id, action_id).status_code == 200

    completed = client.post(
        f"/v1/persons/{person_id}/actions/{action_id}/complete",
        headers=csrf_headers(client),
    )
    rejected = _put_reminder(client, person_id, action_id)
    retained = client.get(_reminder_url(person_id, action_id))

    assert completed.status_code == 200
    assert rejected.status_code == 409
    assert retained.status_code == 200
    assert client.get(_due_url(person_id)).json() == []


def test_due_get_is_owned_zero_write_and_suppresses_completed_actions(client: TestClient) -> None:
    assert register(client, email="reminder-owner@example.com").status_code == 201
    person_id = _person_id(client)
    todo_id = _create_action(client, person_id, title="Open reminder")
    done_id = _create_action(client, person_id, title="Completed reminder")
    assert _put_reminder(client, person_id, todo_id).status_code == 200
    assert _put_reminder(client, person_id, done_id).status_code == 200
    assert (
        client.post(
            f"/v1/persons/{person_id}/actions/{done_id}/complete",
            headers=csrf_headers(client),
        ).status_code
        == 200
    )

    database = Database(DATABASE_URL)

    def snapshot() -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
        with Session(database.engine) as database_session:
            return (
                list(
                    database_session.execute(
                        select(
                            HealthAction.id,
                            HealthAction.status,
                            HealthAction.updated_at,
                        ).order_by(HealthAction.id)
                    ).tuples()
                ),
                list(
                    database_session.execute(
                        select(
                            HealthActionReminder.id,
                            HealthActionReminder.last_acknowledged_local_date,
                            HealthActionReminder.updated_at,
                        ).order_by(HealthActionReminder.id)
                    ).tuples()
                ),
            )

    before = snapshot()
    first = client.get(_due_url(person_id))
    second = client.get(_due_url(person_id))
    after = snapshot()

    assert first.status_code == second.status_code == 200
    assert [row["action_id"] for row in first.json()] == [todo_id]
    assert first.json() == second.json()
    assert after == before


def test_acknowledge_is_local_date_idempotent_and_does_not_complete_action(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_action(client, person_id)
    assert _put_reminder(client, person_id, action_id).status_code == 200

    first = client.post(
        f"{_reminder_url(person_id, action_id)}/acknowledge",
        headers=csrf_headers(client),
    )
    second = client.post(
        f"{_reminder_url(person_id, action_id)}/acknowledge",
        headers=csrf_headers(client),
    )
    action = client.get(f"/v1/persons/{person_id}/actions/{action_id}")

    assert first.status_code == second.status_code == 200
    assert (
        first.json()["last_acknowledged_local_date"]
        == second.json()["last_acknowledged_local_date"]
    )
    assert action.json()["status"] == "todo"
    assert client.get(_due_url(person_id)).json() == []


def test_snooze_is_durable_future_only_and_does_not_change_action(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    action_id = _create_action(client, person_id)
    assert _put_reminder(client, person_id, action_id).status_code == 200
    before_action = client.get(f"/v1/persons/{person_id}/actions/{action_id}").json()
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    snoozed = client.post(
        f"{_reminder_url(person_id, action_id)}/snooze",
        headers=csrf_headers(client),
        json={"until": until},
    )
    repeated = client.post(
        f"{_reminder_url(person_id, action_id)}/snooze",
        headers=csrf_headers(client),
        json={"until": until},
    )
    invalid = client.post(
        f"{_reminder_url(person_id, action_id)}/snooze",
        headers=csrf_headers(client),
        json={"until": datetime.now(UTC).isoformat()},
    )
    after_action = client.get(f"/v1/persons/{person_id}/actions/{action_id}").json()

    assert snoozed.status_code == repeated.status_code == 200
    assert snoozed.json()["snoozed_until"] == repeated.json()["snoozed_until"]
    assert invalid.status_code == 422
    assert after_action["status"] == before_action["status"] == "todo"
    assert after_action["due_at"] == before_action["due_at"]
    assert client.get(_due_url(person_id)).json() == []


def test_concurrent_upserts_keep_one_schedule_per_action(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = uuid.UUID(_person_id(client))
    action_id = uuid.UUID(_create_action(client, str(person_id)))
    with Session(Database(DATABASE_URL).engine) as database_session:
        owner_account_id = database_session.scalar(select(Account.id))
    assert owner_account_id is not None

    def upsert(index: int) -> uuid.UUID:
        with Session(Database(DATABASE_URL).engine) as database_session:
            reminder = services.upsert_health_action_reminder(
                database_session,
                owner_account_id=owner_account_id,
                person_id=person_id,
                action_id=action_id,
                timezone_name="UTC",
                local_time=time(0, index),
            )
            assert reminder is not None
            return reminder.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        reminder_ids = list(executor.map(upsert, [1, 2]))

    with Session(Database(DATABASE_URL).engine) as database_session:
        rows = list(
            database_session.scalars(
                select(HealthActionReminder).where(HealthActionReminder.action_id == action_id)
            )
        )
    assert len(rows) == 1
    assert rows[0].id in reminder_ids


def test_reminder_endpoints_preserve_person_ownership(client: TestClient) -> None:
    assert register(client, email="reminder-owner-a@example.com").status_code == 201
    person_a = _person_id(client)
    action_a = _create_action(client, person_a)
    assert _put_reminder(client, person_a, action_a).status_code == 200

    other = TestClient(client.app, base_url="http://127.0.0.1:3000")
    assert register(other, email="reminder-owner-b@example.com").status_code == 201
    person_b = _person_id(other)

    responses = [
        other.get(_reminder_url(person_b, action_a)),
        other.put(
            _reminder_url(person_b, action_a),
            headers=csrf_headers(other),
            json={"timezone_name": "UTC", "local_time": "00:00"},
        ),
        other.delete(_reminder_url(person_b, action_a), headers=csrf_headers(other)),
        other.post(
            f"{_reminder_url(person_b, action_a)}/acknowledge",
            headers=csrf_headers(other),
        ),
        other.post(
            f"{_reminder_url(person_b, action_a)}/snooze",
            headers=csrf_headers(other),
            json={"until": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        ),
        other.get(_due_url(person_a)),
    ]

    assert all(response.status_code == 404 for response in responses)
