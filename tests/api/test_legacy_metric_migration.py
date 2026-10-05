from __future__ import annotations

import importlib.util

# The schema names are generated from fresh UUID hex values for this synthetic fixture.
# ruff: noqa: S608
import io
import json
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import DATABASE_URL
from healthy.application import legacy_metric_migration as migration
from healthy.application.legacy_metric_export import LegacyExportResult
from healthy.application.legacy_metric_migration import (
    WRITE_CONFIRMATION,
    LegacyMetricMigrationError,
    parse_migration_manifest,
    run_legacy_metric_migration,
    serialize_migration_error,
    serialize_migration_summary,
)
from healthy.infrastructure.models import HealthMetric
from sqlalchemy import create_engine, select, text

EXPECTED_TARGET_REVISION = "20260820_0015"
SYNTHETIC_CSV = (
    b"recorded_at,blood_glucose_mg_dl,note\n2026-01-02T03:04:05Z,123.45,synthetic validation note\n"
)


@dataclass(frozen=True, slots=True)
class SyntheticLegacySource:
    database_url: str
    person_ids: tuple[uuid.UUID, uuid.UUID]


@dataclass(frozen=True, slots=True)
class SyntheticTarget:
    person_ids: tuple[uuid.UUID, uuid.UUID]
    manual_metric_id: uuid.UUID


def _legacy_database_url(schema_name: str) -> str:
    separator = "&" if "?" in DATABASE_URL else "?"
    return f"{DATABASE_URL}{separator}options=-csearch_path%3D{schema_name}"


@pytest.fixture
def synthetic_legacy_source() -> Iterator[SyntheticLegacySource]:
    engine = create_engine(DATABASE_URL)
    schema_name = f"synthetic_metric_migration_{uuid.uuid4().hex}"
    person_ids = (uuid.uuid4(), uuid.uuid4())
    owner_ids = (uuid.uuid4(), uuid.uuid4())
    metric_rows = (
        (
            uuid.uuid4(),
            owner_ids[0],
            person_ids[0],
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            118,
            76,
            64,
            Decimal("123.45"),
            Decimal("61.20"),
            Decimal("7.25"),
            4321,
            "synthetic source alpha",
        ),
        (
            uuid.uuid4(),
            owner_ids[0],
            person_ids[0],
            datetime(2026, 1, 3, 4, 5, 6, tzinfo=UTC),
            None,
            None,
            71,
            None,
            None,
            None,
            8765,
            "synthetic source beta",
        ),
        (
            uuid.uuid4(),
            owner_ids[1],
            person_ids[1],
            datetime(2026, 1, 4, 5, 6, 7, tzinfo=UTC),
            None,
            None,
            None,
            Decimal("88.88"),
            None,
            None,
            None,
            "synthetic source gamma",
        ),
    )

    try:
        with engine.begin() as connection:
            connection.execute(  # noqa: S608
                text(f"CREATE SCHEMA {schema_name}")
            )
            connection.execute(  # noqa: S608
                text(
                    f"""
                    CREATE TABLE {schema_name}.person_profiles (
                        id UUID PRIMARY KEY,
                        owner_user_id UUID NOT NULL,
                        is_default BOOLEAN NOT NULL
                    )
                    """
                )
            )
            connection.execute(  # noqa: S608
                text(
                    f"""
                    CREATE TABLE {schema_name}.health_metrics (
                        id UUID PRIMARY KEY,
                        user_id UUID NOT NULL,
                        subject_profile_id UUID REFERENCES {schema_name}.person_profiles(id),
                        recorded_at TIMESTAMPTZ NOT NULL,
                        systolic_bp INTEGER,
                        diastolic_bp INTEGER,
                        heart_rate INTEGER,
                        blood_glucose NUMERIC(7,2),
                        weight_kg NUMERIC,
                        sleep_hours NUMERIC,
                        steps INTEGER,
                        note TEXT
                    )
                    """
                )
            )
            for person_id, owner_id in zip(person_ids, owner_ids, strict=True):
                connection.execute(
                    text(  # noqa: S608
                        f"INSERT INTO {schema_name}.person_profiles "  # noqa: S608
                        "(id, owner_user_id, is_default) "
                        "VALUES (:id, :owner_id, false)"
                    ),  # noqa: S608
                    {"id": person_id, "owner_id": owner_id},
                )
            for row in metric_rows:
                connection.execute(  # noqa: S608
                    text(  # noqa: S608
                        f"""
                        INSERT INTO {schema_name}.health_metrics
                        (id, user_id, subject_profile_id, recorded_at, systolic_bp,
                         diastolic_bp, heart_rate, blood_glucose, weight_kg,
                         sleep_hours, steps, note)
                        VALUES (:id, :user_id, :subject_profile_id, :recorded_at,
                                :systolic_bp, :diastolic_bp, :heart_rate,
                                :blood_glucose, :weight_kg, :sleep_hours, :steps, :note)
                        """
                    ),  # noqa: S608
                    {
                        "id": row[0],
                        "user_id": row[1],
                        "subject_profile_id": row[2],
                        "recorded_at": row[3],
                        "systolic_bp": row[4],
                        "diastolic_bp": row[5],
                        "heart_rate": row[6],
                        "blood_glucose": row[7],
                        "weight_kg": row[8],
                        "sleep_hours": row[9],
                        "steps": row[10],
                        "note": row[11],
                    },
                )

        yield SyntheticLegacySource(
            database_url=_legacy_database_url(schema_name),
            person_ids=person_ids,
        )
    finally:
        with engine.begin() as connection:
            connection.execute(  # noqa: S608
                text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
            )
        engine.dispose()


@pytest.fixture
def synthetic_target() -> SyntheticTarget:
    engine = create_engine(DATABASE_URL)
    account_id = uuid.uuid4()
    person_ids = (uuid.uuid4(), uuid.uuid4())
    manual_metric_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO accounts "
                "(id, normalized_email, password_hash, status) "
                "VALUES (:id, :email, :password_hash, 'active')"
            ),
            {
                "id": account_id,
                "email": f"{account_id.hex}@synthetic.invalid",
                "password_hash": "synthetic-password-hash",
            },
        )
        connection.execute(
            text(
                "INSERT INTO persons "
                "(id, owner_account_id, display_name, relationship, is_default) "
                "VALUES (:id, :account_id, :display_name, :relationship, :is_default)"
            ),
            [
                {
                    "id": person_ids[0],
                    "account_id": account_id,
                    "display_name": "Synthetic Primary",
                    "relationship": "self",
                    "is_default": True,
                },
                {
                    "id": person_ids[1],
                    "account_id": account_id,
                    "display_name": "Synthetic Secondary",
                    "relationship": "family",
                    "is_default": False,
                },
            ],
        )
        connection.execute(
            text(
                "INSERT INTO health_metrics "
                "(id, person_id, recorded_at, heart_rate_bpm, source_type) "
                "VALUES (:id, :person_id, :recorded_at, :heart_rate, 'manual')"
            ),
            {
                "id": manual_metric_id,
                "person_id": person_ids[0],
                "recorded_at": datetime(2025, 12, 31, 23, 59, tzinfo=UTC),
                "heart_rate": 59,
            },
        )
    engine.dispose()
    return SyntheticTarget(person_ids=person_ids, manual_metric_id=manual_metric_id)


def _manifest(
    legacy_ids: tuple[uuid.UUID, uuid.UUID],
    healthy_ids: tuple[uuid.UUID, uuid.UUID],
) -> str:
    return json.dumps(
        {
            "mappings": [
                {
                    "legacy_person_id": str(legacy_ids[0]),
                    "healthy_person_id": str(healthy_ids[0]),
                },
                {
                    "legacy_person_id": str(legacy_ids[1]),
                    "healthy_person_id": str(healthy_ids[1]),
                },
            ]
        }
    )


def _metric_snapshot() -> list[dict[str, object]]:
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(
                        HealthMetric.id,
                        HealthMetric.person_id,
                        HealthMetric.recorded_at,
                        HealthMetric.heart_rate_bpm,
                        HealthMetric.steps,
                        HealthMetric.weight_kg,
                        HealthMetric.blood_glucose_mg_dl,
                        HealthMetric.sleep_hours,
                        HealthMetric.note,
                        HealthMetric.source_type,
                        HealthMetric.source_record_fingerprint,
                    ).order_by(HealthMetric.id)
                ).mappings()
            ]
    finally:
        engine.dispose()


def _run_execute(
    monkeypatch: pytest.MonkeyPatch,
    source: SyntheticLegacySource,
    target: SyntheticTarget,
    *,
    expected_mapping_count: int = 2,
    expected_total_rows: int = 3,
    expected_target_revision: str = EXPECTED_TARGET_REVISION,
) -> migration.LegacyMetricMigrationSummary:
    monkeypatch.setenv(migration.LEGACY_DATABASE_ENV, source.database_url)
    monkeypatch.setenv(migration.TARGET_DATABASE_ENV, DATABASE_URL)
    return run_legacy_metric_migration(
        _manifest(source.person_ids, target.person_ids),
        mode="execute",
        confirm_write=WRITE_CONFIRMATION,
        expected_mapping_count=expected_mapping_count,
        expected_total_rows=expected_total_rows,
        expected_target_revision=expected_target_revision,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"mappings": []},
        {"mappings": [{"legacy_person_id": "bad", "healthy_person_id": str(uuid.uuid4())}]},
    ],
)
def test_manifest_validation_rejects_empty_or_malformed_input(
    payload: dict[str, object],
) -> None:
    with pytest.raises(LegacyMetricMigrationError) as error_info:
        parse_migration_manifest(json.dumps(payload))
    assert error_info.value.code == "INVALID_MIGRATION_MANIFEST"


def test_manifest_validation_rejects_duplicate_ids_and_sanitizes_errors() -> None:
    legacy_id = uuid.uuid4()
    healthy_id = uuid.uuid4()
    payload = {
        "mappings": [
            {"legacy_person_id": str(legacy_id), "healthy_person_id": str(healthy_id)},
            {"legacy_person_id": str(legacy_id), "healthy_person_id": str(uuid.uuid4())},
        ]
    }
    with pytest.raises(LegacyMetricMigrationError) as error_info:
        parse_migration_manifest(json.dumps(payload))
    error_text = serialize_migration_error(error_info.value)
    assert error_info.value.code == "INVALID_MIGRATION_MANIFEST"
    assert str(legacy_id) not in error_text
    assert str(healthy_id) not in error_text

    duplicate_healthy_payload = {
        "mappings": [
            {"legacy_person_id": str(uuid.uuid4()), "healthy_person_id": str(healthy_id)},
            {"legacy_person_id": str(uuid.uuid4()), "healthy_person_id": str(healthy_id)},
        ]
    }
    with pytest.raises(LegacyMetricMigrationError):
        parse_migration_manifest(json.dumps(duplicate_healthy_payload))


def test_validate_only_uses_canonical_export_and_parser_without_target_or_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    legacy_ids = (uuid.uuid4(), uuid.uuid4())
    healthy_ids = (uuid.uuid4(), uuid.uuid4())
    calls: list[uuid.UUID] = []
    observed_glucose: list[Decimal | None] = []
    original_parser = migration.parse_health_metric_rows

    def fake_export(_database_url: str, person_id: uuid.UUID) -> LegacyExportResult:
        calls.append(person_id)
        return LegacyExportResult(total_rows=1, csv_bytes=SYNTHETIC_CSV)

    def observed_parser(payload: bytes):
        rows = original_parser(payload)
        observed_glucose.append(rows[0].blood_glucose_mg_dl)
        return rows

    monkeypatch.setenv(migration.LEGACY_DATABASE_ENV, "postgresql://synthetic-source.invalid/db")
    monkeypatch.delenv(migration.TARGET_DATABASE_ENV, raising=False)
    monkeypatch.setattr(migration, "export_legacy_health_metrics_csv", fake_export)
    monkeypatch.setattr(migration, "parse_health_metric_rows", observed_parser)

    def forbidden_target_engine(_target_url: str):
        raise AssertionError("validate-only must not create a target engine")

    monkeypatch.setattr(migration, "_create_target_engine", forbidden_target_engine)
    monkeypatch.chdir(tmp_path)
    before_files = set(tmp_path.iterdir())

    summary = run_legacy_metric_migration(
        _manifest(legacy_ids, healthy_ids),
        mode="validate_only",
    )

    assert calls == list(legacy_ids)
    assert observed_glucose == [Decimal("123.45"), Decimal("123.45")]
    assert summary.mapping_count == 2
    assert summary.total_rows == 2
    output = serialize_migration_summary(summary)
    assert (
        output == '{"mode":"validate_only","status":"validated","mapping_count":2,"total_rows":2}'
    )
    assert all(str(identifier) not in output for identifier in (*legacy_ids, *healthy_ids))
    assert set(tmp_path.iterdir()) == before_files


def test_execute_is_atomic_preserves_precision_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_legacy_source: SyntheticLegacySource,
    synthetic_target: SyntheticTarget,
) -> None:
    first = _run_execute(monkeypatch, synthetic_legacy_source, synthetic_target)
    assert first.as_output() == {
        "mode": "execute",
        "status": "executed",
        "mapping_count": 2,
        "total_rows": 3,
        "imported_count": 3,
        "duplicate_count": 0,
    }

    rows_after_first = _metric_snapshot()
    imported_rows = [row for row in rows_after_first if row["source_type"] == "external_csv"]
    assert len(rows_after_first) == 4
    assert len(imported_rows) == 3
    assert Decimal("123.45") in {row["blood_glucose_mg_dl"] for row in imported_rows}
    assert Decimal("88.88") in {row["blood_glucose_mg_dl"] for row in imported_rows}
    assert {row["note"] for row in imported_rows} == {
        "synthetic source alpha",
        "synthetic source beta",
        "synthetic source gamma",
    }
    assert all(row["source_type"] == "external_csv" for row in imported_rows)
    assert all(row["source_record_fingerprint"] for row in imported_rows)
    manual_rows = [row for row in rows_after_first if row["source_type"] == "manual"]
    assert [row["id"] for row in manual_rows] == [synthetic_target.manual_metric_id]

    second = _run_execute(monkeypatch, synthetic_legacy_source, synthetic_target)
    assert second.imported_count == 0
    assert second.duplicate_count == 3
    assert len(_metric_snapshot()) == 4
    output = serialize_migration_summary(second)
    assert all(
        str(value) not in output
        for value in (
            *synthetic_legacy_source.person_ids,
            *synthetic_target.person_ids,
            "synthetic source alpha",
            "synthetic source beta",
            "synthetic source gamma",
        )
    )


def test_confirmation_and_expected_count_gates_precede_target_connection(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_legacy_source: SyntheticLegacySource,
    synthetic_target: SyntheticTarget,
) -> None:
    monkeypatch.setenv(migration.LEGACY_DATABASE_ENV, synthetic_legacy_source.database_url)
    monkeypatch.setenv(migration.TARGET_DATABASE_ENV, "postgresql://synthetic-target.invalid/db")

    with pytest.raises(LegacyMetricMigrationError) as confirmation_error:
        run_legacy_metric_migration(
            _manifest(synthetic_legacy_source.person_ids, synthetic_target.person_ids),
            mode="execute",
            confirm_write="wrong",
            expected_mapping_count=2,
            expected_total_rows=3,
            expected_target_revision=EXPECTED_TARGET_REVISION,
        )
    assert confirmation_error.value.code == "WRITE_CONFIRMATION_REQUIRED"

    def forbidden_target_engine(_target_url: str):
        raise AssertionError("count gates must precede target engine creation")

    monkeypatch.setattr(migration, "_create_target_engine", forbidden_target_engine)
    with pytest.raises(LegacyMetricMigrationError) as count_error:
        run_legacy_metric_migration(
            _manifest(synthetic_legacy_source.person_ids, synthetic_target.person_ids),
            mode="execute",
            confirm_write=WRITE_CONFIRMATION,
            expected_mapping_count=99,
            expected_total_rows=3,
            expected_target_revision=EXPECTED_TARGET_REVISION,
        )
    assert count_error.value.code == "EXPECTED_MAPPING_COUNT_MISMATCH"
    assert len(_metric_snapshot()) == 1

    with pytest.raises(LegacyMetricMigrationError) as row_count_error:
        run_legacy_metric_migration(
            _manifest(synthetic_legacy_source.person_ids, synthetic_target.person_ids),
            mode="execute",
            confirm_write=WRITE_CONFIRMATION,
            expected_mapping_count=2,
            expected_total_rows=99,
            expected_target_revision=EXPECTED_TARGET_REVISION,
        )
    assert row_count_error.value.code == "EXPECTED_TOTAL_ROWS_MISMATCH"
    assert len(_metric_snapshot()) == 1


def test_target_revision_and_person_preflight_leave_zero_new_rows(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_legacy_source: SyntheticLegacySource,
    synthetic_target: SyntheticTarget,
) -> None:
    with pytest.raises(LegacyMetricMigrationError) as revision_error:
        _run_execute(
            monkeypatch,
            synthetic_legacy_source,
            synthetic_target,
            expected_target_revision="synthetic-wrong-revision",
        )
    assert revision_error.value.code == "TARGET_SCHEMA_REVISION_MISMATCH"
    assert len(_metric_snapshot()) == 1

    missing_target = (synthetic_target.person_ids[0], uuid.uuid4())
    monkeypatch.setenv(migration.LEGACY_DATABASE_ENV, synthetic_legacy_source.database_url)
    monkeypatch.setenv(migration.TARGET_DATABASE_ENV, DATABASE_URL)
    with pytest.raises(LegacyMetricMigrationError) as person_error:
        run_legacy_metric_migration(
            _manifest(synthetic_legacy_source.person_ids, missing_target),
            mode="execute",
            confirm_write=WRITE_CONFIRMATION,
            expected_mapping_count=2,
            expected_total_rows=3,
            expected_target_revision=EXPECTED_TARGET_REVISION,
        )
    assert person_error.value.code == "TARGET_PERSON_NOT_FOUND"
    assert person_error.value.mapping_index == 1
    assert len(_metric_snapshot()) == 1


def test_later_mapping_failure_rolls_back_earlier_staged_inserts(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_legacy_source: SyntheticLegacySource,
    synthetic_target: SyntheticTarget,
) -> None:
    original_import = migration.HealthMetricRepository.import_external_metrics
    call_count = 0

    def fail_second_import(database_session, person_id, rows):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("synthetic target failure")
        return original_import(database_session, person_id, rows)

    monkeypatch.setattr(
        migration.HealthMetricRepository,
        "import_external_metrics",
        fail_second_import,
    )
    with pytest.raises(LegacyMetricMigrationError) as error_info:
        _run_execute(monkeypatch, synthetic_legacy_source, synthetic_target)

    assert error_info.value.code == "TARGET_IMPORT_FAILED"
    assert error_info.value.mapping_index == 1
    assert len(_metric_snapshot()) == 1


def test_source_validation_failure_prevents_target_connection_and_writes(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_legacy_source: SyntheticLegacySource,
    synthetic_target: SyntheticTarget,
) -> None:
    original_export = migration.export_legacy_health_metrics_csv

    def invalid_second_export(database_url: str, person_id: uuid.UUID) -> LegacyExportResult:
        if person_id == synthetic_legacy_source.person_ids[1]:
            return LegacyExportResult(
                total_rows=1,
                csv_bytes=b"recorded_at,blood_glucose_mg_dl\n2026-01-01T00:00:00Z,invalid\n",
            )
        return original_export(database_url, person_id)

    monkeypatch.setattr(migration, "export_legacy_health_metrics_csv", invalid_second_export)
    monkeypatch.setenv(migration.TARGET_DATABASE_ENV, DATABASE_URL)

    def forbidden_target_engine(_target_url: str):
        raise AssertionError("source validation failure must precede target connection")

    monkeypatch.setattr(migration, "_create_target_engine", forbidden_target_engine)
    with pytest.raises(LegacyMetricMigrationError) as error_info:
        _run_execute(monkeypatch, synthetic_legacy_source, synthetic_target)

    assert error_info.value.code == "LEGACY_COMPATIBILITY_FAILED"
    assert error_info.value.mapping_index == 1
    assert len(_metric_snapshot()) == 1


def test_cli_failure_is_one_compact_privacy_safe_json_object(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_path = Path(__file__).parents[2] / "scripts" / "migrate_legacy_health_metrics.py"
    cli_spec = importlib.util.spec_from_file_location("migration_cli", cli_path)
    assert cli_spec is not None and cli_spec.loader is not None
    cli = importlib.util.module_from_spec(cli_spec)
    cli_spec.loader.exec_module(cli)

    legacy_id = uuid.uuid4()
    healthy_id = uuid.uuid4()
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "mappings": [
                        {
                            "legacy_person_id": str(legacy_id),
                            "healthy_person_id": str(healthy_id),
                        },
                        {
                            "legacy_person_id": str(legacy_id),
                            "healthy_person_id": str(uuid.uuid4()),
                        },
                    ]
                }
            )
        ),
    )
    monkeypatch.setenv(
        migration.LEGACY_DATABASE_ENV,
        "postgresql://synthetic_user:synthetic_password@synthetic.invalid/db",
    )

    assert cli.main(["--validate-only"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err == '{"status":"error","code":"INVALID_MIGRATION_MANIFEST","mapping_index":1}\n'
    )
    assert str(legacy_id) not in captured.err
    assert str(healthy_id) not in captured.err
    assert "synthetic_password" not in captured.err
