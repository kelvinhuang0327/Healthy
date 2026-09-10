from __future__ import annotations

import os
import subprocess
import sys
import threading
import time as time_module
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from conftest import DATABASE_URL, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.application import notification_delivery
from healthy.domain import notifications as notifications_domain
from healthy.infrastructure.config import Settings
from healthy.infrastructure.database import Database
from healthy.infrastructure.email import SMTPEmailTransport
from healthy.infrastructure.models import (
    HealthAction,
    HealthActionReminder,
    NotificationDelivery,
)
from healthy.main import create_app
from sqlalchemy import select
from sqlalchemy.orm import Session

ORIGIN = "http://127.0.0.1:3000"
FIXED_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


class RecordingTransport:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []
        self.lock = threading.Lock()

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        with self.lock:
            self.messages.append({"recipient": recipient, "subject": subject, "body": body})


class FailingTransport:
    def send(self, *, recipient: str, subject: str, body: str) -> None:
        del recipient, subject, body
        raise RuntimeError("synthetic provider response must not persist")


@pytest.fixture
def email_client() -> TestClient:
    settings = Settings(
        environment="test",
        database_url=DATABASE_URL,
        cookie_secure=False,
        allowed_origins=frozenset({ORIGIN}),
        csrf_secret=os.urandom(32),
        email_notifications_enabled=True,
        smtp_host="smtp.invalid",
        smtp_port=587,
        smtp_from_address="no-reply@healthy.invalid",
        smtp_starttls=True,
    )
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        yield client


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _create_action(client: TestClient, person_id: str, title: str = "Drink water") -> str:
    response = client.post(
        f"/v1/persons/{person_id}/actions",
        headers=csrf_headers(client),
        json={"title": title, "description": "Private action description"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _reminder_url(person_id: str, action_id: str) -> str:
    return f"/v1/persons/{person_id}/actions/{action_id}/reminder"


def _email_url(person_id: str, action_id: str) -> str:
    return f"{_reminder_url(person_id, action_id)}/channels/email"


def _configure_reminder(client: TestClient, person_id: str, action_id: str) -> dict:
    response = client.put(
        _reminder_url(person_id, action_id),
        headers=csrf_headers(client),
        json={"timezone_name": "UTC", "local_time": "00:00"},
    )
    assert response.status_code == 200
    return response.json()


def _engine():
    return Database(DATABASE_URL).engine


def _delivery_rows() -> list[NotificationDelivery]:
    with Session(_engine()) as database_session:
        return list(database_session.scalars(select(NotificationDelivery)))


def test_config_defaults_disabled_and_enabled_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HEALTHY_EMAIL_NOTIFICATIONS_ENABLED",
        "HEALTHY_SMTP_HOST",
        "HEALTHY_SMTP_PORT",
        "HEALTHY_SMTP_FROM_ADDRESS",
        "HEALTHY_SMTP_STARTTLS",
        "HEALTHY_SMTP_USERNAME",
        "HEALTHY_SMTP_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    disabled = Settings.from_env()
    assert disabled.email_notifications_enabled is False
    assert disabled.email_delivery_available is False

    monkeypatch.setenv("HEALTHY_EMAIL_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("HEALTHY_SMTP_HOST", "smtp.invalid")
    monkeypatch.setenv("HEALTHY_SMTP_FROM_ADDRESS", "no-reply@healthy.invalid")
    enabled = Settings.from_env()
    assert enabled.email_delivery_available is True

    monkeypatch.setenv("HEALTHY_SMTP_USERNAME", "smtp-user")
    with pytest.raises(RuntimeError, match="configured together"):
        Settings.from_env()

    monkeypatch.setenv("HEALTHY_SMTP_PASSWORD", "super-secret")
    assert "super-secret" not in repr(Settings.from_env())

    monkeypatch.setenv("HEALTHY_ENV", "production")
    monkeypatch.setenv("HEALTHY_DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("HEALTHY_ALLOWED_ORIGINS", "https://healthy.example")
    monkeypatch.setenv("HEALTHY_CSRF_SECRET", os.urandom(32).hex())
    monkeypatch.setenv("HEALTHY_COOKIE_SECURE", "true")
    monkeypatch.setenv("HEALTHY_SMTP_STARTTLS", "false")
    with pytest.raises(RuntimeError, match="secure SMTP transport"):
        Settings.from_env()


def test_preference_defaults_off_is_explicit_idempotent_and_reload_safe(
    email_client: TestClient,
) -> None:
    assert register(email_client, email="notification-owner@example.com").status_code == 201
    person_id = _person_id(email_client)
    action_id = _create_action(email_client, person_id)
    initial = _configure_reminder(email_client, person_id, action_id)
    assert initial["email_enabled"] is False
    assert email_client.get("/v1/notification-capabilities").json() == {"email_available": True}

    enabled = email_client.put(
        _email_url(person_id, action_id),
        headers=csrf_headers(email_client),
        json={"enabled": True},
    )
    repeated = email_client.put(
        _email_url(person_id, action_id),
        headers=csrf_headers(email_client),
        json={"enabled": True},
    )
    assert enabled.status_code == repeated.status_code == 200
    assert enabled.json()["email_enabled"] is True
    assert repeated.json() == enabled.json()
    assert email_client.get(_reminder_url(person_id, action_id)).json()["email_enabled"] is True

    disabled = email_client.put(
        _email_url(person_id, action_id),
        headers=csrf_headers(email_client),
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["email_enabled"] is False


def test_preference_requires_existing_open_owned_reminder_and_capability(
    client: TestClient,
    email_client: TestClient,
) -> None:
    assert register(client, email="unavailable@example.com").status_code == 201
    person_id = _person_id(client)
    action_id = _create_action(client, person_id)
    missing_reminder = client.put(
        _email_url(person_id, action_id),
        headers=csrf_headers(client),
        json={"enabled": True},
    )
    assert missing_reminder.status_code == 404

    _configure_reminder(client, person_id, action_id)
    unavailable = client.put(
        _email_url(person_id, action_id),
        headers=csrf_headers(client),
        json={"enabled": True},
    )
    assert unavailable.status_code == 409

    assert register(email_client, email="done-owner@example.com").status_code == 201
    done_person_id = _person_id(email_client)
    done_action_id = _create_action(email_client, done_person_id, title="Completed action")
    _configure_reminder(email_client, done_person_id, done_action_id)
    complete = email_client.post(
        f"/v1/persons/{done_person_id}/actions/{done_action_id}/complete",
        headers=csrf_headers(email_client),
    )
    assert complete.status_code == 200
    rejected = email_client.put(
        _email_url(done_person_id, done_action_id),
        headers=csrf_headers(email_client),
        json={"enabled": True},
    )
    assert rejected.status_code == 409


def _create_enabled_delivery(
    email_client: TestClient, title: str = "Private action title"
) -> tuple[str, str]:
    person_id = _person_id(email_client)
    action_id = _create_action(email_client, person_id, title=title)
    _configure_reminder(email_client, person_id, action_id)
    enabled = email_client.put(
        _email_url(person_id, action_id),
        headers=csrf_headers(email_client),
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    return person_id, action_id


def test_enqueue_is_due_local_date_unique_and_privacy_minimized(
    email_client: TestClient,
) -> None:
    assert register(email_client, email="enqueue-owner@example.com").status_code == 201
    _, action_id = _create_enabled_delivery(email_client)
    with Session(_engine()) as database_session:
        first = notification_delivery.enqueue_due_email_deliveries(database_session, now=FIXED_NOW)
        second = notification_delivery.enqueue_due_email_deliveries(database_session, now=FIXED_NOW)
        assert len(first) == 1
        assert second == []
        rows = list(database_session.scalars(select(NotificationDelivery)))
        action = database_session.scalar(
            select(HealthAction).where(HealthAction.id == uuid.UUID(action_id))
        )
        assert action is not None
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].reminder_local_date == date(2026, 8, 13)
        persisted = " ".join(str(value) for value in rows[0].__dict__.values())
        assert "enqueue-owner@example.com" not in persisted
        assert action.title not in persisted
        assert (action.description or "") not in persisted

        next_day = notification_delivery.enqueue_due_email_deliveries(
            database_session,
            now=FIXED_NOW + timedelta(days=1),
        )
        assert len(next_day) == 1
        assert len(list(database_session.scalars(select(NotificationDelivery)))) == 2


def test_concurrent_enqueue_and_claim_are_idempotent(
    email_client: TestClient,
) -> None:
    assert register(email_client, email="concurrent-owner@example.com").status_code == 201
    _create_enabled_delivery(email_client, title="Concurrent private action")

    def enqueue_once() -> int:
        with Session(_engine()) as database_session:
            return len(
                notification_delivery.enqueue_due_email_deliveries(database_session, now=FIXED_NOW)
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        created_counts = list(executor.map(lambda _: enqueue_once(), [1, 2]))
    assert sum(created_counts) == 1
    assert len(_delivery_rows()) == 1

    def claim_once() -> uuid.UUID | None:
        with Session(_engine()) as database_session:
            delivery = notification_delivery.NotificationDeliveryRepository.claim_next_pending(
                database_session,
                claimed_at=FIXED_NOW,
            )
            if delivery is None:
                return None
            claimed_id = delivery.id
            time_module.sleep(0.2)
            database_session.commit()
            return claimed_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed_ids = list(executor.map(lambda _: claim_once(), [1, 2]))
    assert [claimed_id for claimed_id in claimed_ids if claimed_id is not None].__len__() == 1
    assert _delivery_rows()[0].status == "sending"


def test_dispatch_success_failure_and_privacy_boundary(
    email_client: TestClient,
) -> None:
    assert register(email_client, email="dispatch-owner@example.com").status_code == 201
    person_id, action_id = _create_enabled_delivery(email_client)
    transport = RecordingTransport()
    with Session(_engine()) as database_session:
        assert (
            len(notification_delivery.enqueue_due_email_deliveries(database_session, now=FIXED_NOW))
            == 1
        )
        result = notification_delivery.dispatch_pending_email_deliveries(
            database_session,
            transport=transport,
            now=FIXED_NOW,
        )
        assert result.sent == 1
        row = database_session.scalar(select(NotificationDelivery))
        assert row is not None
        assert row.status == "sent"
        assert row.sent_at is not None
        assert row.failure_code is None
        assert transport.messages == [
            {
                "recipient": "dispatch-owner@example.com",
                "subject": notifications_domain.GENERIC_EMAIL_SUBJECT,
                "body": notifications_domain.GENERIC_EMAIL_BODY,
            }
        ]
        message_text = str(transport.messages[0])
        assert "Private action title" not in message_text
        assert "Private action description" not in message_text
        assert (
            database_session.scalar(
                select(HealthAction).where(HealthAction.id == uuid.UUID(action_id))
            ).status
            == "todo"
        )
        assert (
            database_session.scalar(
                select(HealthActionReminder).where(
                    HealthActionReminder.action_id == uuid.UUID(action_id)
                )
            ).last_acknowledged_local_date
            is None
        )

    assert register(email_client, email="failure-owner@example.com").status_code == 201
    _, failure_action_id = _create_enabled_delivery(email_client, title="Failure private action")
    with Session(_engine()) as database_session:
        assert (
            len(notification_delivery.enqueue_due_email_deliveries(database_session, now=FIXED_NOW))
            == 1
        )
        result = notification_delivery.dispatch_pending_email_deliveries(
            database_session,
            transport=FailingTransport(),
            now=FIXED_NOW,
        )
        assert result.failed == 1
        failed = database_session.scalar(
            select(NotificationDelivery)
            .join(
                HealthActionReminder,
                HealthActionReminder.id == NotificationDelivery.reminder_id,
            )
            .where(HealthActionReminder.action_id == uuid.UUID(failure_action_id))
        )
        assert failed is not None
        assert failed.status == "failed"
        assert failed.failure_code == "transport_error"
        assert "synthetic provider" not in str(failed.__dict__)
        repeated = notification_delivery.dispatch_pending_email_deliveries(
            database_session,
            transport=RecordingTransport(),
            now=FIXED_NOW,
        )
        assert repeated.claimed == 0


@pytest.mark.parametrize("state", ["disabled", "acknowledged", "snoozed", "completed"])
def test_dispatch_revalidates_current_state_before_send(
    email_client: TestClient,
    state: str,
) -> None:
    assert register(email_client, email=f"revalidate-{state}@example.com").status_code == 201
    person_id, action_id = _create_enabled_delivery(email_client, title=f"{state} private action")
    with Session(_engine()) as database_session:
        assert (
            len(notification_delivery.enqueue_due_email_deliveries(database_session, now=FIXED_NOW))
            == 1
        )

    with Session(_engine()) as database_session:
        reminder = database_session.scalar(
            select(HealthActionReminder).where(
                HealthActionReminder.action_id == uuid.UUID(action_id)
            )
        )
        action = database_session.scalar(
            select(HealthAction).where(HealthAction.id == uuid.UUID(action_id))
        )
        assert reminder is not None and action is not None
        if state == "disabled":
            reminder.email_enabled = False
        elif state == "acknowledged":
            reminder.last_acknowledged_local_date = date(2026, 8, 13)
        elif state == "snoozed":
            reminder.snoozed_until = FIXED_NOW + timedelta(hours=1)
        else:
            action.status = "done"
            action.completed_at = FIXED_NOW
        database_session.commit()

    transport = RecordingTransport()
    with Session(_engine()) as database_session:
        result = notification_delivery.dispatch_pending_email_deliveries(
            database_session,
            transport=transport,
            now=FIXED_NOW,
        )
        assert result.cancelled == 1
        assert transport.messages == []
        row = database_session.scalar(select(NotificationDelivery))
        assert row is not None
        assert row.status == "cancelled"


def test_stale_sending_becomes_unknown_without_retry(email_client: TestClient) -> None:
    assert register(email_client, email="stale-owner@example.com").status_code == 201
    _create_enabled_delivery(email_client, title="Stale private action")
    with Session(_engine()) as database_session:
        assert (
            len(notification_delivery.enqueue_due_email_deliveries(database_session, now=FIXED_NOW))
            == 1
        )
        delivery = database_session.scalar(select(NotificationDelivery))
        assert delivery is not None
        delivery.status = "sending"
        delivery.attempt_count = 1
        delivery.claimed_at = FIXED_NOW - timedelta(hours=1)
        database_session.commit()
        assert (
            notification_delivery.reconcile_stale_sending_deliveries(
                database_session,
                now=FIXED_NOW,
            )
            == 1
        )
        assert delivery.status == "unknown"
        result = notification_delivery.dispatch_pending_email_deliveries(
            database_session,
            transport=RecordingTransport(),
            now=FIXED_NOW,
        )
        assert result.claimed == 0


def test_one_shot_worker_tick_is_bounded_and_send_is_injected(
    email_client: TestClient,
) -> None:
    assert register(email_client, email="worker-owner@example.com").status_code == 201
    _create_enabled_delivery(email_client, title="Worker private action")
    settings = Settings(
        environment="test",
        database_url=DATABASE_URL,
        cookie_secure=False,
        allowed_origins=frozenset({ORIGIN}),
        csrf_secret=os.urandom(32),
        email_notifications_enabled=True,
        smtp_host="smtp.invalid",
        smtp_from_address="no-reply@healthy.invalid",
        smtp_starttls=True,
    )
    with Session(_engine()) as database_session:
        preview = notification_delivery.process_notification_delivery_tick(
            database_session,
            settings=settings,
            send=False,
            now=FIXED_NOW,
        )
        assert preview.enqueued == 1
        assert preview.sent == 0
        assert preview.skipped_send == 1
        assert database_session.scalar(select(NotificationDelivery)).status == "pending"

        sent = notification_delivery.process_notification_delivery_tick(
            database_session,
            settings=settings,
            send=True,
            transport=RecordingTransport(),
            now=FIXED_NOW,
        )
        assert sent.claimed == 1
        assert sent.sent == 1


def test_cli_without_send_performs_no_external_send() -> None:
    source = Path(__file__).resolve().parents[2] / "scripts" / "process_notification_deliveries.py"
    environment = os.environ.copy()
    environment.update(
        {
            "HEALTHY_ENV": "test",
            "HEALTHY_DATABASE_URL": DATABASE_URL,
            "HEALTHY_COOKIE_SECURE": "false",
            "HEALTHY_ALLOWED_ORIGINS": ORIGIN,
            "HEALTHY_EMAIL_NOTIFICATIONS_ENABLED": "false",
        }
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(source)],
        cwd=source.parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout.startswith("status=capability_unavailable ")
    assert result.stderr == ""


def test_smtp_adapter_uses_generic_payload_without_live_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="test",
        database_url=DATABASE_URL,
        cookie_secure=False,
        allowed_origins=frozenset({ORIGIN}),
        csrf_secret=os.urandom(32),
        email_notifications_enabled=True,
        smtp_host="smtp.invalid",
        smtp_port=2525,
        smtp_from_address="sender@healthy.invalid",
        smtp_starttls=True,
        smtp_username="username",
        smtp_password="password",
    )
    calls: list[tuple[str, object]] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, *, timeout: float) -> None:
            calls.append(("connect", (host, port, timeout)))

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def starttls(self) -> None:
            calls.append(("starttls", None))

        def login(self, username: str, password: str) -> None:
            calls.append(("login", (username, password)))

        def send_message(self, message: object) -> None:
            calls.append(("send", message))

    monkeypatch.setattr("healthy.infrastructure.email.smtplib.SMTP", FakeSMTP)
    SMTPEmailTransport(settings).send(
        recipient="owner@example.com",
        subject=notifications_domain.GENERIC_EMAIL_SUBJECT,
        body=notifications_domain.GENERIC_EMAIL_BODY,
    )
    assert calls[0] == ("connect", ("smtp.invalid", 2525, 10.0))
    assert calls[1] == ("starttls", None)
    assert calls[2] == ("login", ("username", "password"))
    message = calls[3][1]
    assert message["To"] == "owner@example.com"
    assert message["Subject"] == notifications_domain.GENERIC_EMAIL_SUBJECT
    assert message.get_content().strip() == notifications_domain.GENERIC_EMAIL_BODY


def test_cli_source_is_bounded_and_requires_explicit_send_flag() -> None:
    source = Path(__file__).resolve().parents[2] / "scripts" / "process_notification_deliveries.py"
    text = source.read_text(encoding="utf-8")
    assert "--send" in text
    assert "while True" not in text
    assert "SMTPEmailTransport(settings) if args.send else None" in text
