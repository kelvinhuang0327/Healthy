from __future__ import annotations

import os
import secrets
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from healthy.infrastructure.config import Settings
from healthy.main import create_app
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ["HEALTHY_DATABASE_URL"]
ORIGIN = "http://127.0.0.1:3000"


@pytest.fixture(autouse=True)
def clean_database() -> Iterator[None]:
    engine = create_engine(DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE sessions, persons, accounts CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE sessions, persons, accounts CASCADE"))
    engine.dispose()


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(
        environment="test",
        database_url=DATABASE_URL,
        cookie_secure=False,
        allowed_origins=frozenset({ORIGIN}),
        csrf_secret=secrets.token_bytes(32),
    )
    with TestClient(create_app(settings), base_url=ORIGIN) as test_client:
        yield test_client


def csrf_headers(client: TestClient, *, origin: str = ORIGIN) -> dict[str, str]:
    csrf = client.cookies.get("healthy_csrf")
    return {"Origin": origin, "X-CSRF-Token": csrf or ""}


def register(
    client: TestClient,
    *,
    email: str = "owner@example.com",
    password: str = "Synthetic-Password-42",
    display_name: str = "Owner Person",
):
    return client.post(
        "/v1/accounts",
        headers={"Origin": ORIGIN},
        json={
            "email": email,
            "password": password,
            "display_name": display_name,
        },
    )
