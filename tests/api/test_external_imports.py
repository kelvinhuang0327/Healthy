from __future__ import annotations

from uuid import UUID

from conftest import DATABASE_URL, register
from fastapi.testclient import TestClient
from healthy.application import services
from healthy.domain.external_imports import (
    SOURCE_TYPE_EXTERNAL_CSV,
    SUPPORT_HEADER_ORDER,
    parse_health_metric_rows,
)
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import HealthMetric
from sqlalchemy import select


def test_duplicate_external_csv_rows_persist_once_and_replay_is_idempotent(
    client: TestClient,
) -> None:
    registration = register(client, email="csv-import@example.com")
    assert registration.status_code == 201
    person_id = UUID(registration.json()["default_person"]["id"])

    header = ",".join(SUPPORT_HEADER_ORDER)
    row = "2026-01-01T00:00:00Z,,,72,,,,,Synthetic row"
    payload = f"{header}\n{row}\n{row}\n".encode()

    first_parse = parse_health_metric_rows(payload)
    replay_parse = parse_health_metric_rows(payload)

    assert len(first_parse) == len(replay_parse) == 2
    assert first_parse[0].source_record_fingerprint == first_parse[1].source_record_fingerprint
    assert [row.source_record_fingerprint for row in replay_parse] == [
        row.source_record_fingerprint for row in first_parse
    ]

    database = Database(DATABASE_URL)
    with next(database.sessions()) as database_session:
        first_import = services.import_external_health_metrics_csv(
            database_session,
            person_id=person_id,
            payload=payload,
        )
        replay = services.import_external_health_metrics_csv(
            database_session,
            person_id=person_id,
            payload=payload,
        )
        persisted = list(
            database_session.scalars(
                select(HealthMetric).where(HealthMetric.person_id == person_id)
            )
        )

    assert first_import.source_type == replay.source_type == SOURCE_TYPE_EXTERNAL_CSV
    assert (first_import.total_rows, first_import.imported_count, first_import.duplicate_count) == (
        2,
        1,
        1,
    )
    assert (replay.total_rows, replay.imported_count, replay.duplicate_count) == (2, 0, 2)
    assert len(persisted) == 1
    assert persisted[0].heart_rate_bpm == 72
    assert persisted[0].note == "Synthetic row"
    assert persisted[0].source_type == SOURCE_TYPE_EXTERNAL_CSV
    assert persisted[0].source_record_fingerprint == first_parse[0].source_record_fingerprint
    database.engine.dispose()
