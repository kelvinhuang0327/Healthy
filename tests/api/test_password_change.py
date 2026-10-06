from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import DATABASE_URL, ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.application import services
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import Account, SessionRecord
from healthy.infrastructure.security import hash_password, verify_password
from sqlalchemy import func, select, update

OLD = "Synthetic-Password-42"
NEW = "Synthetic-New-Password-73"
PATH = "/v1/accounts/current/password"


def login(client: TestClient, password: str, email: str = "owner@example.com"):
    return client.post(
        "/v1/sessions", headers={"Origin": ORIGIN}, json={"email": email, "password": password}
    )


def change(client: TestClient, current: str = OLD, new: str = NEW):
    return client.post(
        PATH, headers=csrf_headers(client), json={"current_password": current, "new_password": new}
    )


def test_password_change_revokes_current_and_other_sessions_but_not_other_accounts(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    old_cookies = dict(client.cookies)
    other = TestClient(client.app)
    stranger = TestClient(client.app)
    assert login(other, OLD).status_code == 200
    assert register(stranger, email="stranger@example.com").status_code == 201
    assert change(client).status_code == 204
    assert client.get("/v1/session").status_code == other.get("/v1/session").status_code == 401
    assert stranger.get("/v1/session").status_code == 200
    replay = TestClient(client.app)
    replay.cookies.update(old_cookies)
    assert change(replay).status_code == 401
    assert login(client, OLD).status_code == 401
    assert login(client, NEW).status_code == 200
    assert client.cookies.get("healthy_session") != old_cookies["healthy_session"]
    with next(Database(DATABASE_URL).sessions()) as db:
        account = db.scalar(select(Account).where(Account.normalized_email == "owner@example.com"))
        assert account is not None and account.password_hash != NEW
        assert verify_password(NEW, account.password_hash)
        assert (
            db.scalar(
                select(func.count())
                .select_from(SessionRecord)
                .where(SessionRecord.account_id == account.id, SessionRecord.revoked_at.is_(None))
            )
            == 1
        )


@pytest.mark.parametrize(
    ("current", "new", "status"), [("Wrong-Synthetic-Password", NEW, 400), (OLD, OLD, 422)]
)
def test_rejected_change_preserves_password_and_session(
    client: TestClient, current: str, new: str, status: int
) -> None:
    assert register(client).status_code == 201
    assert change(client, current, new).status_code == status
    assert client.get("/v1/session").status_code == 200
    assert login(TestClient(client.app), OLD).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": ORIGIN}, {"Origin": "https://attacker.invalid", "X-CSRF-Token": "synthetic"}],
)
def test_password_change_preserves_origin_and_csrf_boundary(
    client: TestClient, headers: dict[str, str]
) -> None:
    assert register(client).status_code == 201
    response = client.post(
        PATH, headers=headers, json={"current_password": OLD, "new_password": NEW}
    )
    assert response.status_code == 403
    assert client.get("/v1/session").status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {"current_password": "", "new_password": NEW},
        {"current_password": OLD, "new_password": "Short-Sentinel"[:8]},
        {"current_password": OLD, "new_password": "Long-Sentinel-" * 15},
        {"current_password": OLD, "new_password": NEW, "account_id": "foreign-synthetic-id"},
    ],
)
def test_password_validation_does_not_echo_credentials(
    client: TestClient, payload: dict[str, str]
) -> None:
    assert register(client).status_code == 201
    response = client.post(PATH, headers=csrf_headers(client), json=payload)
    assert response.status_code == 422 and response.json() == {"detail": "Invalid request"}
    assert OLD not in response.text and NEW not in response.text
    assert client.get("/v1/session").status_code == 200


def test_password_and_revocations_rollback_together(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert register(client).status_code == 201
    with next(Database(DATABASE_URL).sessions()) as db:
        authenticated = services.resolve_session(
            db, raw_token=client.cookies.get("healthy_session")
        )
        assert authenticated is not None
        execute = db.execute

        def fail_revocations(statement, *args, **kwargs):
            if getattr(getattr(statement, "table", None), "name", None) == "sessions":
                raise RuntimeError("synthetic revocation interruption")
            return execute(statement, *args, **kwargs)

        monkeypatch.setattr(db, "execute", fail_revocations)
        with pytest.raises(RuntimeError, match="synthetic revocation interruption"):
            services.change_password(db, authenticated, current_password=OLD, new_password=NEW)
    assert client.get("/v1/session").status_code == 200
    assert login(TestClient(client.app), OLD).status_code == 200
    assert login(TestClient(client.app), NEW).status_code == 401


def test_login_cannot_issue_session_after_verifying_replaced_password(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert register(client).status_code == 201
    original = services.verify_password
    replacement = hash_password(NEW)

    def verify_then_replace(password: str, encoded: str | None) -> bool:
        valid = original(password, encoded)
        with next(Database(DATABASE_URL).sessions()) as db:
            account = db.scalar(select(Account))
            assert account is not None
            db.execute(
                update(Account).where(Account.id == account.id).values(password_hash=replacement)
            )
            db.execute(
                update(SessionRecord)
                .where(SessionRecord.account_id == account.id)
                .values(revoked_at=datetime.now(UTC))
            )
            db.commit()
        return valid

    monkeypatch.setattr(services, "verify_password", verify_then_replace)
    assert login(TestClient(client.app), OLD).status_code == 401
    monkeypatch.setattr(services, "verify_password", original)
    assert login(TestClient(client.app), NEW).status_code == 200


def test_concurrent_password_changes_do_not_overwrite_winner(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert register(client).status_code == 201
    winner = TestClient(client.app)
    assert login(winner, OLD).status_code == 200
    original = services.hash_password
    fired = False

    def replace_during_hash(password: str) -> str:
        nonlocal fired
        if not fired:
            fired = True
            assert change(winner, OLD, "Synthetic-Winning-Password-91").status_code == 204
        return original(password)

    monkeypatch.setattr(services, "hash_password", replace_during_hash)
    assert change(client).status_code == 400
    assert client.get("/v1/session").status_code == 401
    monkeypatch.setattr(services, "hash_password", original)
    assert login(TestClient(client.app), "Synthetic-Winning-Password-91").status_code == 200
    assert login(TestClient(client.app), NEW).status_code == 401
