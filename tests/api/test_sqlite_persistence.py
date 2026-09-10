from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from conftest import DATABASE_URL, ROOT, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.infrastructure.config import Settings
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import Account, HealthMetric, Person, SessionRecord
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


def test_default_database_url_is_absolute_persistent_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HEALTHY_DATABASE_URL", raising=False)

    settings = Settings.from_env()
    parsed_url = make_url(settings.database_url)

    assert parsed_url.drivername == "sqlite+pysqlite"
    assert parsed_url.database is not None
    assert Path(parsed_url.database).is_absolute()
    assert Path(parsed_url.database).name == "healthy.db"


def test_fresh_sqlite_database_upgrades_to_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "fresh-healthy.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("HEALTHY_DATABASE_URL", database_url)
    alembic_config = Config(str(ROOT / "migrations" / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(ROOT / "migrations"))

    command.upgrade(alembic_config, "head")

    database = Database(database_url)
    inspector = inspect(database.engine)
    assert set(inspector.get_table_names()) >= {
        "accounts",
        "sessions",
        "persons",
        "health_metrics",
        "symptom_logs",
        "health_actions",
        "health_action_outcomes",
        "health_reports",
        "health_report_observations",
        "health_action_reminders",
    }
    with database.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "20260910_0016"
        )
        partial_index_sql = connection.scalar(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'index' AND name = 'uq_persons_one_default_per_account'"
            )
        )
    assert partial_index_sql is not None
    assert "WHERE is_default" in partial_index_sql
    database.engine.dispose()


def test_sqlite_precision_loss_downgrade_preserves_head_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "precision-loss.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    monkeypatch.setenv("HEALTHY_DATABASE_URL", database_url)
    alembic_config = Config(str(ROOT / "migrations" / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(ROOT / "migrations"))

    command.upgrade(alembic_config, "head")

    database = Database(database_url)
    account_id = uuid4()
    person_id = uuid4()
    with database.engine.begin() as connection:
        connection.execute(
            Account.__table__.insert().values(
                id=account_id,
                normalized_email="precision-loss@example.com",
                password_hash="test-hash",
                status="active",
            )
        )
        connection.execute(
            Person.__table__.insert().values(
                id=person_id,
                owner_account_id=account_id,
                display_name="Precision Loss",
                relationship="self",
                is_default=True,
            )
        )
        connection.execute(
            HealthMetric.__table__.insert().values(
                id=uuid4(),
                person_id=person_id,
                recorded_at=datetime(2026, 8, 1, 8, 0, tzinfo=UTC),
                blood_glucose_mg_dl=Decimal("95.55"),
                source_type="external_csv",
                source_record_fingerprint="a" * 64,
            )
        )

    with pytest.raises(RuntimeError, match="BLOOD_GLUCOSE_DOWNGRADE_PRECISION_LOSS"):
        command.downgrade(alembic_config, "20260818_0014")

    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "20260910_0016"
        )
        table_sql = connection.scalar(
            text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'health_metrics'")
        )
        assert table_sql is not None
        assert "source_record_consistent" in table_sql
        assert "uq_health_metrics_person_source_record_fingerprint" in table_sql
    database.engine.dispose()


def test_sqlite_foreign_keys_reject_orphans_and_cascade_account_delete(
    client: TestClient,
) -> None:
    database = Database(DATABASE_URL)
    with pytest.raises(IntegrityError):
        with database.engine.begin() as connection:
            connection.execute(
                SessionRecord.__table__.insert().values(
                    id=uuid4(),
                    account_id=uuid4(),
                    token_hash="f" * 64,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )

    registration = register(client, email="sqlite-cascade@example.com")
    assert registration.status_code == 201
    account_id = UUID(registration.json()["account"]["id"])
    person_id = UUID(registration.json()["default_person"]["id"])
    metric = client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json={
            "recorded_at": datetime.now(UTC).isoformat(),
            "weight_kg": 73.25,
        },
    )
    assert metric.status_code == 201

    with database.engine.begin() as connection:
        connection.execute(delete(Account).where(Account.id == account_id))

    with next(database.sessions()) as database_session:
        assert database_session.scalar(select(func.count()).select_from(Account)) == 0
        assert database_session.scalar(select(func.count()).select_from(SessionRecord)) == 0
        assert database_session.scalar(select(func.count()).select_from(Person)) == 0
        assert database_session.scalar(select(func.count()).select_from(HealthMetric)) == 0
    database.engine.dispose()


def test_uuid_and_numeric_values_round_trip_exactly(client: TestClient) -> None:
    registration = register(client, email="sqlite-roundtrip@example.com")
    assert registration.status_code == 201
    account_id = UUID(registration.json()["account"]["id"])
    person_id = UUID(registration.json()["default_person"]["id"])
    metric_response = client.post(
        f"/v1/persons/{person_id}/metrics",
        headers=csrf_headers(client),
        json={
            "recorded_at": datetime.now(UTC).isoformat(),
            "weight_kg": 73.25,
            "blood_glucose_mg_dl": 101.5,
            "sleep_hours": 7.25,
        },
    )
    assert metric_response.status_code == 201
    metric_id = UUID(metric_response.json()["id"])

    database = Database(DATABASE_URL)
    with next(database.sessions()) as database_session:
        account = database_session.get(Account, account_id)
        person = database_session.get(Person, person_id)
        metric = database_session.get(HealthMetric, metric_id)

        assert account is not None and account.id == account_id
        assert person is not None and person.id == person_id
        assert metric is not None and metric.id == metric_id
        assert isinstance(metric.id, UUID)
        assert metric.weight_kg == Decimal("73.25")
        assert metric.blood_glucose_mg_dl == Decimal("101.5")
        assert metric.sleep_hours == Decimal("7.25")
    database.engine.dispose()
