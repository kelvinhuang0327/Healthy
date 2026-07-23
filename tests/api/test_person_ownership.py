from __future__ import annotations

import threading
from datetime import datetime

from conftest import DATABASE_URL, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import Account, Person, SessionRecord
from healthy.infrastructure.security import hash_password
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError


def test_person_queries_are_owner_scoped_and_foreign_access_is_404(
    client: TestClient,
) -> None:
    assert register(client, email="owner-a@example.com", display_name="Owner A").status_code == 201
    person_a = client.get("/v1/persons").json()[0]

    other = TestClient(client.app, base_url="http://127.0.0.1:3000")
    assert register(other, email="owner-b@example.com", display_name="Owner B").status_code == 201
    person_b = other.get("/v1/persons").json()[0]

    assert {row["id"] for row in client.get("/v1/persons").json()} == {person_a["id"]}
    assert {row["id"] for row in other.get("/v1/persons").json()} == {person_b["id"]}
    foreign = client.get(f"/v1/persons/{person_b['id']}")
    assert foreign.status_code == 404
    assert person_b["id"] not in foreign.text
    assert "Owner B" not in foreign.text


def test_repeated_gets_do_not_write_or_refresh(client: TestClient) -> None:
    assert register(client).status_code == 201
    created = client.post(
        "/v1/persons",
        headers=csrf_headers(client),
        json={"display_name": "Child", "relationship": "child"},
    )
    assert created.status_code == 201
    default_person_id = client.get("/v1/persons").json()[0]["id"]

    database = Database(DATABASE_URL)

    def snapshot() -> tuple[int, int, int, list[tuple[object, datetime]], list[object]]:
        with next(database.sessions()) as database_session:
            person_times = list(
                database_session.execute(
                    select(Person.id, Person.updated_at).order_by(Person.id)
                ).tuples()
            )
            session_expiry = list(
                database_session.scalars(
                    select(SessionRecord.expires_at).order_by(SessionRecord.id)
                )
            )
            return (
                database_session.scalar(select(func.count()).select_from(Account)) or 0,
                database_session.scalar(select(func.count()).select_from(Person)) or 0,
                database_session.scalar(select(func.count()).select_from(SessionRecord)) or 0,
                person_times,
                session_expiry,
            )

    before = snapshot()
    for _ in range(3):
        assert client.get("/v1/session").status_code == 200
        assert client.get("/v1/persons").status_code == 200
        assert client.get(f"/v1/persons/{default_person_id}").status_code == 200
    after = snapshot()
    assert after == before


def test_postgres_constraint_allows_only_one_concurrent_default_person() -> None:
    database = Database(DATABASE_URL)
    with next(database.sessions()) as database_session:
        account = Account(
            normalized_email="concurrent@example.com",
            password_hash=hash_password("Synthetic-Password-42"),
            status="active",
        )
        database_session.add(account)
        database_session.commit()
        account_id = account.id

    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def insert_default(display_name: str) -> None:
        with next(database.sessions()) as database_session:
            database_session.add(
                Person(
                    owner_account_id=account_id,
                    display_name=display_name,
                    relationship="self",
                    is_default=True,
                )
            )
            barrier.wait()
            try:
                database_session.commit()
                outcomes.append("committed")
            except IntegrityError:
                database_session.rollback()
                outcomes.append("rejected")

    threads = [
        threading.Thread(target=insert_default, args=("Default A",)),
        threading.Thread(target=insert_default, args=("Default B",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert sorted(outcomes) == ["committed", "rejected"]
    with next(database.sessions()) as database_session:
        defaults = database_session.scalar(
            select(func.count())
            .select_from(Person)
            .where(
                Person.owner_account_id == account_id,
                Person.is_default.is_(True),
            )
        )
        assert defaults == 1
