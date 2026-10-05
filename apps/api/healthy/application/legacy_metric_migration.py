from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from healthy.application.legacy_metric_export import (
    LegacyExportCompatibilityError,
    LegacyExportError,
    export_legacy_health_metrics_csv,
)
from healthy.domain.external_imports import (
    HealthMetricCsvImportValidationError,
    ParsedHealthMetricRow,
    build_import_summary,
    parse_health_metric_rows,
)
from healthy.infrastructure.models import Person
from healthy.infrastructure.repositories import HealthMetricRepository

MigrationMode = Literal["validate_only", "execute"]

LEGACY_DATABASE_ENV = "HEALTHY_MIGRATION_LEGACY_DATABASE_URL"
TARGET_DATABASE_ENV = "HEALTHY_MIGRATION_TARGET_DATABASE_URL"
WRITE_CONFIRMATION = "HEALTHY_LEGACY_METRIC_MIGRATION_V1"


class LegacyMetricMigrationError(Exception):
    """A privacy-safe, stable migration failure."""

    def __init__(self, code: str, *, mapping_index: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.mapping_index = mapping_index


@dataclass(frozen=True, slots=True)
class LegacyMetricMigrationMapping:
    legacy_person_id: uuid.UUID
    healthy_person_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LegacyMetricMigrationSummary:
    mode: MigrationMode
    status: Literal["validated", "executed"]
    mapping_count: int
    total_rows: int
    imported_count: int = 0
    duplicate_count: int = 0

    def as_output(self) -> dict[str, int | str]:
        output: dict[str, int | str] = {
            "mode": self.mode,
            "status": self.status,
            "mapping_count": self.mapping_count,
            "total_rows": self.total_rows,
        }
        if self.mode == "execute":
            output["imported_count"] = self.imported_count
            output["duplicate_count"] = self.duplicate_count
        return output


def _invalid_manifest(*, mapping_index: int | None = None) -> LegacyMetricMigrationError:
    return LegacyMetricMigrationError(
        "INVALID_MIGRATION_MANIFEST",
        mapping_index=mapping_index,
    )


def _parse_uuid(value: object) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except (AttributeError, ValueError):  # fmt: skip
        return None


def parse_migration_manifest(
    payload: bytes | str,
) -> tuple[LegacyMetricMigrationMapping, ...]:
    try:
        if isinstance(payload, bytes):
            document = json.loads(payload.decode("utf-8"))
        elif isinstance(payload, str):
            document = json.loads(payload)
        else:
            raise TypeError
    except Exception:
        raise _invalid_manifest() from None

    if not isinstance(document, Mapping):
        raise _invalid_manifest()
    raw_mappings = document.get("mappings")
    if not isinstance(raw_mappings, list) or not raw_mappings:
        raise _invalid_manifest()

    seen_legacy_ids: set[uuid.UUID] = set()
    seen_healthy_ids: set[uuid.UUID] = set()
    mappings: list[LegacyMetricMigrationMapping] = []
    for mapping_index, raw_mapping in enumerate(raw_mappings):
        if not isinstance(raw_mapping, Mapping):
            raise _invalid_manifest(mapping_index=mapping_index)

        legacy_person_id = _parse_uuid(raw_mapping.get("legacy_person_id"))
        healthy_person_id = _parse_uuid(raw_mapping.get("healthy_person_id"))
        if legacy_person_id is None or healthy_person_id is None:
            raise _invalid_manifest(mapping_index=mapping_index)
        if legacy_person_id in seen_legacy_ids or healthy_person_id in seen_healthy_ids:
            raise _invalid_manifest(mapping_index=mapping_index)

        seen_legacy_ids.add(legacy_person_id)
        seen_healthy_ids.add(healthy_person_id)
        mappings.append(
            LegacyMetricMigrationMapping(
                legacy_person_id=legacy_person_id,
                healthy_person_id=healthy_person_id,
            )
        )

    return tuple(mappings)


def _required_environment_value(name: str, *, error_code: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise LegacyMetricMigrationError(error_code)
    return value


def _source_rows(
    legacy_database_url: str,
    mappings: tuple[LegacyMetricMigrationMapping, ...],
) -> tuple[tuple[tuple[LegacyMetricMigrationMapping, list[ParsedHealthMetricRow]], ...], int]:
    batches: list[tuple[LegacyMetricMigrationMapping, list[ParsedHealthMetricRow]]] = []
    total_rows = 0
    for mapping_index, mapping in enumerate(mappings):
        try:
            export_result = export_legacy_health_metrics_csv(
                legacy_database_url,
                mapping.legacy_person_id,
            )
        except LegacyExportCompatibilityError:
            raise LegacyMetricMigrationError(
                "LEGACY_COMPATIBILITY_FAILED",
                mapping_index=mapping_index,
            ) from None
        except LegacyExportError:
            raise LegacyMetricMigrationError(
                "LEGACY_EXPORT_FAILED",
                mapping_index=mapping_index,
            ) from None
        except Exception:
            raise LegacyMetricMigrationError(
                "LEGACY_EXPORT_FAILED",
                mapping_index=mapping_index,
            ) from None

        try:
            exported_row_count = export_result.total_rows
            parsed_rows = parse_health_metric_rows(export_result.csv_bytes)
        except HealthMetricCsvImportValidationError:
            raise LegacyMetricMigrationError(
                "LEGACY_COMPATIBILITY_FAILED",
                mapping_index=mapping_index,
            ) from None
        except Exception:
            raise LegacyMetricMigrationError(
                "LEGACY_COMPATIBILITY_FAILED",
                mapping_index=mapping_index,
            ) from None

        if exported_row_count != len(parsed_rows):
            raise LegacyMetricMigrationError(
                "LEGACY_COMPATIBILITY_FAILED",
                mapping_index=mapping_index,
            )

        batches.append((mapping, parsed_rows))
        total_rows += len(parsed_rows)

    return tuple(batches), total_rows


def _create_target_engine(target_database_url: str) -> Engine:
    return create_engine(target_database_url, pool_pre_ping=True)


def _preflight_target(
    database_session: Session,
    mappings: tuple[LegacyMetricMigrationMapping, ...],
    expected_target_revision: str,
) -> None:
    try:
        revisions = list(
            database_session.execute(text("SELECT version_num FROM alembic_version")).scalars()
        )
    except Exception:
        raise LegacyMetricMigrationError("TARGET_SCHEMA_REVISION_MISMATCH") from None

    if len(revisions) != 1 or str(revisions[0]) != expected_target_revision:
        raise LegacyMetricMigrationError("TARGET_SCHEMA_REVISION_MISMATCH")

    healthy_person_ids = {mapping.healthy_person_id for mapping in mappings}
    try:
        existing_person_ids = set(
            database_session.scalars(select(Person.id).where(Person.id.in_(healthy_person_ids)))
        )
    except Exception:
        raise LegacyMetricMigrationError("TARGET_PERSON_NOT_FOUND") from None

    missing_mapping_index = next(
        (
            mapping_index
            for mapping_index, mapping in enumerate(mappings)
            if mapping.healthy_person_id not in existing_person_ids
        ),
        None,
    )
    if missing_mapping_index is not None:
        raise LegacyMetricMigrationError(
            "TARGET_PERSON_NOT_FOUND",
            mapping_index=missing_mapping_index,
        )


def _execute_target(
    target_database_url: str,
    mappings: tuple[LegacyMetricMigrationMapping, ...],
    batches: tuple[tuple[LegacyMetricMigrationMapping, list[ParsedHealthMetricRow]], ...],
    expected_target_revision: str,
    total_rows: int,
) -> LegacyMetricMigrationSummary:
    try:
        target_engine = _create_target_engine(target_database_url)
    except Exception:
        raise LegacyMetricMigrationError("TARGET_DATABASE_DIALECT_UNSUPPORTED") from None

    try:
        if target_engine.dialect.name != "postgresql":
            raise LegacyMetricMigrationError("TARGET_DATABASE_DIALECT_UNSUPPORTED")

        imported_count = 0
        duplicate_count = 0
        with Session(target_engine) as database_session:
            try:
                with database_session.begin():
                    _preflight_target(
                        database_session,
                        mappings,
                        expected_target_revision,
                    )

                    for mapping_index, (mapping, rows) in enumerate(batches):
                        try:
                            inserted_count = HealthMetricRepository.import_external_metrics(
                                database_session,
                                person_id=mapping.healthy_person_id,
                                rows=rows,
                            )
                            import_summary = build_import_summary(
                                rows=rows,
                                inserted_count=inserted_count,
                            )
                        except Exception:
                            raise LegacyMetricMigrationError(
                                "TARGET_IMPORT_FAILED",
                                mapping_index=mapping_index,
                            ) from None

                        imported_count += import_summary.imported_count
                        duplicate_count += import_summary.duplicate_count
            except LegacyMetricMigrationError:
                database_session.rollback()
                raise
            except Exception:
                database_session.rollback()
                raise LegacyMetricMigrationError("TARGET_IMPORT_FAILED") from None

        return LegacyMetricMigrationSummary(
            mode="execute",
            status="executed",
            mapping_count=len(mappings),
            total_rows=total_rows,
            imported_count=imported_count,
            duplicate_count=duplicate_count,
        )
    finally:
        target_engine.dispose()


def run_legacy_metric_migration(
    manifest_payload: bytes | str,
    *,
    mode: MigrationMode = "validate_only",
    confirm_write: str | None = None,
    expected_mapping_count: int | None = None,
    expected_total_rows: int | None = None,
    expected_target_revision: str | None = None,
) -> LegacyMetricMigrationSummary:
    if mode not in {"validate_only", "execute"}:
        raise LegacyMetricMigrationError("INVALID_MIGRATION_MODE")

    mappings = parse_migration_manifest(manifest_payload)
    if mode == "execute":
        if confirm_write != WRITE_CONFIRMATION:
            raise LegacyMetricMigrationError("WRITE_CONFIRMATION_REQUIRED")
        if expected_mapping_count is None:
            raise LegacyMetricMigrationError("EXPECTED_MAPPING_COUNT_REQUIRED")
        if expected_total_rows is None:
            raise LegacyMetricMigrationError("EXPECTED_TOTAL_ROWS_REQUIRED")
        if expected_target_revision is None or not expected_target_revision.strip():
            raise LegacyMetricMigrationError("EXPECTED_TARGET_REVISION_REQUIRED")

    legacy_database_url = _required_environment_value(
        LEGACY_DATABASE_ENV,
        error_code="LEGACY_DATABASE_URL_REQUIRED",
    )

    batches, total_rows = _source_rows(legacy_database_url, mappings)

    if mode == "validate_only":
        return LegacyMetricMigrationSummary(
            mode="validate_only",
            status="validated",
            mapping_count=len(mappings),
            total_rows=total_rows,
        )

    assert expected_mapping_count is not None
    assert expected_total_rows is not None
    assert expected_target_revision is not None
    if expected_mapping_count != len(mappings):
        raise LegacyMetricMigrationError("EXPECTED_MAPPING_COUNT_MISMATCH")
    if expected_total_rows != total_rows:
        raise LegacyMetricMigrationError("EXPECTED_TOTAL_ROWS_MISMATCH")

    target_database_url = _required_environment_value(
        TARGET_DATABASE_ENV,
        error_code="TARGET_DATABASE_URL_REQUIRED",
    )
    return _execute_target(
        target_database_url,
        mappings,
        batches,
        expected_target_revision,
        total_rows,
    )


def serialize_migration_summary(summary: LegacyMetricMigrationSummary) -> str:
    return json.dumps(summary.as_output(), separators=(",", ":"))


def serialize_migration_error(error: LegacyMetricMigrationError) -> str:
    output: dict[str, int | str] = {
        "status": "error",
        "code": error.code,
    }
    if error.mapping_index is not None:
        output["mapping_index"] = error.mapping_index
    return json.dumps(output, separators=(",", ":"))
