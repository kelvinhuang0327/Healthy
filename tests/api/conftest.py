from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from healthy.infrastructure.config import Settings
from healthy.infrastructure.database import Database
from healthy.main import create_app
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEST_DATABASE_PATH = ROOT / ".healthy-test.db"
DATABASE_URL = os.getenv(
    "HEALTHY_DATABASE_URL",
    f"sqlite+pysqlite:///{DEFAULT_TEST_DATABASE_PATH}",
)
os.environ.setdefault("HEALTHY_DATABASE_URL", DATABASE_URL)
ORIGIN = "http://127.0.0.1:3000"


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    alembic_config = Config(str(ROOT / "migrations" / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(alembic_config, "head")
    yield


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> Iterator[None]:
    database = Database(DATABASE_URL)
    with database.engine.begin() as connection:
        connection.execute(text("DELETE FROM accounts"))
    yield
    with database.engine.begin() as connection:
        connection.execute(text("DELETE FROM accounts"))
    database.engine.dispose()


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
