from __future__ import annotations

import importlib.util
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
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEST_DATABASE_PATH = ROOT / ".healthy-test.db"
DATABASE_URL = os.getenv(
    "HEALTHY_DATABASE_URL",
    f"sqlite+pysqlite:///{DEFAULT_TEST_DATABASE_PATH}",
)
os.environ.setdefault("HEALTHY_DATABASE_URL", DATABASE_URL)
ORIGIN = "http://127.0.0.1:3000"
LEGACY_POSTGRES_DATABASE_URL = os.getenv("HEALTHY_LEGACY_POSTGRES_DATABASE_URL")


def legacy_postgres_skip_reason() -> str | None:
    opt_in = os.getenv("HEALTHY_RUN_LEGACY_POSTGRES_TESTS", "").strip().casefold()
    if opt_in not in {"1", "true", "yes", "on"}:
        return (
            "legacy PostgreSQL tooling tests are opt-in; run "
            "make legacy-postgres-test LEGACY_POSTGRES_DATABASE_URL=..."
        )
    if not LEGACY_POSTGRES_DATABASE_URL:
        return "set HEALTHY_LEGACY_POSTGRES_DATABASE_URL to a synthetic PostgreSQL source URL"
    if not LEGACY_POSTGRES_DATABASE_URL.startswith("postgresql+psycopg://"):
        return "HEALTHY_LEGACY_POSTGRES_DATABASE_URL must use postgresql+psycopg://"
    try:
        driver_available = importlib.util.find_spec("psycopg") is not None
    except ModuleNotFoundError:
        driver_available = False
    if not driver_available:
        return "install the optional driver with uv run --extra legacy-postgres"
    return None


def legacy_postgres_engine() -> Engine:
    if LEGACY_POSTGRES_DATABASE_URL is None:
        pytest.skip(legacy_postgres_skip_reason() or "legacy PostgreSQL URL is unavailable")

    engine: Engine | None = None
    try:
        engine = create_engine(LEGACY_POSTGRES_DATABASE_URL)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as error:
        if engine is not None:
            engine.dispose()
        pytest.skip(
            "synthetic PostgreSQL fixture is unavailable; provide a reachable "
            f"fixture (connection error: {error.__class__.__name__})"
        )
    assert engine is not None
    return engine


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
