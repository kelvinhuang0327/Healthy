from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from conftest import DATABASE_URL, register
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

PREVIOUS_REVISION = "20260818_0014"
NEW_REVISION = "20260820_0015"
TWO_DECIMAL_METRIC_ID = uuid.UUID("00000000-0000-0000-0000-000000000901")
ONE_DECIMAL_METRIC_ID = uuid.UUID("00000000-0000-0000-0000-000000000902")
TWO_DECIMAL_FINGERPRINT = "a" * 64
ONE_DECIMAL_FINGERPRINT = "b" * 64


def _alembic_config() -> Config:
    return Config("migrations/alembic.ini")


def _migration_revision(engine) -> str:
    with engine.connect() as connection:
        return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


def _glucose_type(engine) -> tuple[int, int]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'health_metrics' "
                "AND column_name = 'blood_glucose_mg_dl'"
            )
        ).one()
    return int(row[0]), int(row[1])


def _metric_snapshot(engine, metric_id: uuid.UUID) -> tuple[Decimal, str]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT blood_glucose_mg_dl, source_record_fingerprint "
                "FROM health_metrics WHERE id = :metric_id"
            ),
            {"metric_id": metric_id},
        ).one()
    return Decimal(str(row[0])), str(row[1])


def _insert_metric(
    engine,
    *,
    metric_id: uuid.UUID,
    person_id: str,
    glucose: Decimal,
    fingerprint: str,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO health_metrics "
                "(id, person_id, recorded_at, blood_glucose_mg_dl, source_type, "
                "source_record_fingerprint) "
                "VALUES (:id, :person_id, :recorded_at, :glucose, 'external_csv', :fingerprint)"
            ),
            {
                "id": metric_id,
                "person_id": person_id,
                "recorded_at": datetime(2026, 8, 1, 8, 0, tzinfo=UTC),
                "glucose": glucose,
                "fingerprint": fingerprint,
            },
        )


def test_blood_glucose_precision_migration_is_lossless_and_fail_closed(
    client: TestClient,
) -> None:
    assert register(client, email="migration-owner@example.com").status_code == 201
    person_id = client.get("/v1/persons").json()[0]["id"]
    engine = create_engine(DATABASE_URL)
    config = _alembic_config()

    try:
        command.upgrade(config, "head")
        assert _migration_revision(engine) == NEW_REVISION
        assert _glucose_type(engine) == (6, 2)

        _insert_metric(
            engine,
            metric_id=TWO_DECIMAL_METRIC_ID,
            person_id=person_id,
            glucose=Decimal("95.55"),
            fingerprint=TWO_DECIMAL_FINGERPRINT,
        )
        with pytest.raises(RuntimeError, match="BLOOD_GLUCOSE_DOWNGRADE_PRECISION_LOSS"):
            command.downgrade(config, PREVIOUS_REVISION)

        assert _migration_revision(engine) == NEW_REVISION
        assert _glucose_type(engine) == (6, 2)
        assert _metric_snapshot(engine, TWO_DECIMAL_METRIC_ID) == (
            Decimal("95.55"),
            TWO_DECIMAL_FINGERPRINT,
        )

        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM health_metrics WHERE id = :metric_id"),
                {"metric_id": TWO_DECIMAL_METRIC_ID},
            )

        _insert_metric(
            engine,
            metric_id=ONE_DECIMAL_METRIC_ID,
            person_id=person_id,
            glucose=Decimal("95.5"),
            fingerprint=ONE_DECIMAL_FINGERPRINT,
        )
        command.downgrade(config, PREVIOUS_REVISION)
        assert _migration_revision(engine) == PREVIOUS_REVISION
        assert _glucose_type(engine) == (5, 1)
        assert _metric_snapshot(engine, ONE_DECIMAL_METRIC_ID) == (
            Decimal("95.5"),
            ONE_DECIMAL_FINGERPRINT,
        )

        command.upgrade(config, "head")
        assert _migration_revision(engine) == NEW_REVISION
        assert _glucose_type(engine) == (6, 2)
        assert _metric_snapshot(engine, ONE_DECIMAL_METRIC_ID) == (
            Decimal("95.5"),
            ONE_DECIMAL_FINGERPRINT,
        )
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM health_metrics WHERE id IN (:two_decimal_id, :one_decimal_id)"),
                {
                    "two_decimal_id": TWO_DECIMAL_METRIC_ID,
                    "one_decimal_id": ONE_DECIMAL_METRIC_ID,
                },
            )
        engine.dispose()
