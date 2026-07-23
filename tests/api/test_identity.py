from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from conftest import DATABASE_URL, ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.application import services
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import Account, Person, SessionRecord
from healthy.infrastructure.security import verify_password
from sqlalchemy import func, select


def test_registration_is_transactional_and_returns_safe_summaries(
    client: TestClient,
) -> None:
    plaintext = "Synthetic-Password-42"
    response = register(
        client,
        email="  Owner@Example.COM ",
        password=plaintext,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["account"]["normalized_email"] == "owner@example.com"
    assert body["default_person"]["relationship"] == "self"
    assert body["default_person"]["is_default"] is True
    assert "token" not in response.text.casefold()
    assert "password" not in response.text.casefold()

    database = Database(DATABASE_URL)
    with next(database.sessions()) as database_session:
        account = database_session.scalar(select(Account))
        session_record = database_session.scalar(select(SessionRecord))
        assert account is not None
        assert session_record is not None
        assert database_session.scalar(select(func.count()).select_from(Account)) == 1
        assert database_session.scalar(select(func.count()).select_from(Person)) == 1
        assert database_session.scalar(select(func.count()).select_from(SessionRecord)) == 1
        assert account.password_hash != plaintext
        assert verify_password(plaintext, account.password_hash)
        raw_cookie = client.cookies.get("healthy_session")
        assert raw_cookie
        assert session_record.token_hash == hashlib.sha256(raw_cookie.encode()).hexdigest()
        assert raw_cookie not in session_record.token_hash

    set_cookie = response.headers.get_list("set-cookie")
    session_cookie = next(value for value in set_cookie if value.startswith("healthy_session="))
    assert "HttpOnly" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert "Path=/" in session_cookie
    assert "Max-Age=28800" in session_cookie


def test_duplicate_normalized_email_fails_without_partial_records(
    client: TestClient,
) -> None:
    assert register(client, email="duplicate@example.com").status_code == 201
    duplicate = register(client, email="  DUPLICATE@example.com ")
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "Unable to create account"}

    database = Database(DATABASE_URL)
    with next(database.sessions()) as database_session:
        assert database_session.scalar(select(func.count()).select_from(Account)) == 1
        assert database_session.scalar(select(func.count()).select_from(Person)) == 1
        assert database_session.scalar(select(func.count()).select_from(SessionRecord)) == 1


def test_registration_rolls_back_if_session_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(DATABASE_URL)

    def fail_session_creation(*_args, **_kwargs):
        raise RuntimeError("synthetic session failure")

    monkeypatch.setattr(services, "_session_record", fail_session_creation)
    with next(database.sessions()) as database_session:
        with pytest.raises(RuntimeError, match="synthetic session failure"):
            services.register_account(
                database_session,
                email="rollback@example.com",
                password="Synthetic-Password-42",
                display_name="Rollback Person",
                session_max_age_seconds=28_800,
            )
        assert database_session.scalar(select(func.count()).select_from(Account)) == 0
        assert database_session.scalar(select(func.count()).select_from(Person)) == 0
        assert database_session.scalar(select(func.count()).select_from(SessionRecord)) == 0


def test_login_error_does_not_enumerate_accounts(client: TestClient) -> None:
    assert register(client).status_code == 201
    client.cookies.clear()
    wrong_password = client.post(
        "/v1/sessions",
        headers={"Origin": ORIGIN},
        json={
            "email": "owner@example.com",
            "password": "Incorrect-Synthetic-Password",
        },
    )
    missing_account = client.post(
        "/v1/sessions",
        headers={"Origin": ORIGIN},
        json={
            "email": "missing@example.com",
            "password": "Incorrect-Synthetic-Password",
        },
    )
    assert wrong_password.status_code == missing_account.status_code == 401
    assert wrong_password.json() == missing_account.json() == {"detail": "Invalid credentials"}


@pytest.mark.parametrize(
    ("path", "payload", "password"),
    [
        (
            "/v1/accounts",
            {
                "email": "validation-register@example.com",
                "password": "P0-Secret",
                "display_name": "Validation Person",
            },
            "P0-Secret",
        ),
        (
            "/v1/sessions",
            {
                "email": "validation-login@example.com",
                "password": "Login-Password-Sentinel-" * 50,
            },
            "Login-Password-Sentinel-" * 50,
        ),
    ],
)
def test_validation_errors_never_echo_submitted_passwords(
    client: TestClient,
    path: str,
    payload: dict[str, str],
    password: str,
) -> None:
    response = client.post(path, headers={"Origin": ORIGIN}, json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert password not in response.text


def test_expired_and_revoked_sessions_are_rejected(client: TestClient) -> None:
    assert register(client).status_code == 201
    database = Database(DATABASE_URL)
    with next(database.sessions()) as database_session:
        record = database_session.scalar(select(SessionRecord))
        assert record is not None
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        database_session.commit()
    assert client.get("/v1/session").status_code == 401

    client.cookies.clear()
    login_response = client.post(
        "/v1/sessions",
        headers={"Origin": ORIGIN},
        json={
            "email": "owner@example.com",
            "password": "Synthetic-Password-42",
        },
    )
    assert login_response.status_code == 200
    with next(database.sessions()) as database_session:
        active = database_session.scalar(
            select(SessionRecord)
            .where(SessionRecord.revoked_at.is_(None))
            .order_by(SessionRecord.created_at.desc())
        )
        assert active is not None
        active.revoked_at = datetime.now(UTC)
        database_session.commit()
    assert client.get("/v1/session").status_code == 401


def test_logout_requires_origin_and_csrf_then_revokes(client: TestClient) -> None:
    assert register(client).status_code == 201
    missing_csrf = client.delete(
        "/v1/sessions/current",
        headers={"Origin": ORIGIN},
    )
    assert missing_csrf.status_code == 403
    cross_origin = client.delete(
        "/v1/sessions/current",
        headers=csrf_headers(client, origin="https://attacker.invalid"),
    )
    assert cross_origin.status_code == 403
    logout = client.delete(
        "/v1/sessions/current",
        headers=csrf_headers(client),
    )
    assert logout.status_code == 204
    assert client.get("/v1/session").status_code == 401

    database = Database(DATABASE_URL)
    with next(database.sessions()) as database_session:
        record = database_session.scalar(select(SessionRecord))
        assert record is not None
        assert record.revoked_at is not None


def test_create_person_requires_valid_csrf(client: TestClient) -> None:
    assert register(client).status_code == 201
    missing = client.post(
        "/v1/persons",
        headers={"Origin": ORIGIN},
        json={"display_name": "Child", "relationship": "child"},
    )
    invalid = client.post(
        "/v1/persons",
        headers={"Origin": ORIGIN, "X-CSRF-Token": "invalid"},
        json={"display_name": "Child", "relationship": "child"},
    )
    allowed = client.post(
        "/v1/persons",
        headers=csrf_headers(client),
        json={"display_name": "Child", "relationship": "child"},
    )
    assert missing.status_code == invalid.status_code == 403
    assert allowed.status_code == 201
    assert allowed.json()["is_default"] is False
