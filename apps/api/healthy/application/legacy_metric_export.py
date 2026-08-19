from __future__ import annotations

import csv
import io
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from healthy.domain.external_imports import (
    MAX_CSV_BYTES,
    MAX_CSV_ROWS,
    HealthMetricCsvImportValidationError,
    parse_health_metric_rows,
)


class LegacyExportError(Exception):
    """Base exception for legacy health metric export errors."""


class LegacyPersonNotFoundError(LegacyExportError):
    """Raised when the specified legacy Person profile is not found."""

    def __init__(self, person_id: str) -> None:
        super().__init__(f"Legacy person not found: {person_id}")
        self.person_id = person_id
        self.code = "LEGACY_PERSON_NOT_FOUND"


class LegacySchemaIncompatibleError(LegacyExportError):
    """Raised when the legacy database schema does not match expected tables/columns."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "INCOMPATIBLE_LEGACY_SCHEMA"


class LegacyExportCompatibilityError(LegacyExportError):
    """Raised when legacy data fails Healthy compatibility validation."""

    def __init__(
        self,
        *,
        code: str,
        row_number: int | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(code, row_number, field)
        self.code = code
        self.row_number = row_number
        self.field = field

    def detail(self) -> dict[str, object]:
        return {
            "code": self.code,
            "row": self.row_number,
            "field": self.field,
        }

    def __str__(self) -> str:
        if self.row_number is None:
            return f"Legacy export compatibility error: code={self.code}"
        if self.field is None:
            return f"Legacy export compatibility error at row {self.row_number}: code={self.code}"
        return (
            f"Legacy export compatibility error at row {self.row_number}, "
            f"field {self.field}: code={self.code}"
        )


class LegacyDatabaseError(LegacyExportError):
    """Raised when a database operational or connection error occurs."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "LEGACY_DATABASE_ERROR"


@dataclass(frozen=True, slots=True)
class LegacyExportResult:
    total_rows: int
    csv_bytes: bytes


def _normalize_datetime_str(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, datetime):
        if raw.tzinfo is not None and raw.tzinfo.utcoffset(raw) is not None:
            return raw.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return raw.isoformat()
    if isinstance(raw, str):
        cleaned = raw.strip()
        if not cleaned:
            return ""
        try:
            normalized = cleaned.replace("Z", "+00:00") if cleaned.endswith("Z") else cleaned
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None:
                return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
            return cleaned
        except Exception:
            return cleaned
    return str(raw)


def _format_decimal_value(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, Decimal):
        return str(raw)
    if isinstance(raw, (int, float)):
        return str(raw)
    return str(raw).strip()


def _format_int_value(raw: Any) -> str:
    if raw is None:
        return ""
    return str(raw).strip()


def _format_str_value(raw: Any) -> str:
    if raw is None:
        return ""
    return str(raw)


def _create_read_only_engine(database_url: str) -> Engine:
    return create_engine(database_url)


def export_legacy_health_metrics_csv(
    legacy_database_url: str,
    legacy_person_id: uuid.UUID | str,
) -> LegacyExportResult:
    person_id_str = str(legacy_person_id)
    try:
        uuid_obj = uuid.UUID(person_id_str)
    except (ValueError, AttributeError) as exc:
        raise LegacyExportCompatibilityError(
            code="INVALID_LEGACY_PERSON_ID",
            row_number=None,
            field="legacy_person_id",
        ) from exc

    engine = _create_read_only_engine(legacy_database_url)
    try:
        with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                conn.execution_options(postgresql_readonly=True)
                conn.execute(text("SET TRANSACTION READ ONLY;"))
            elif engine.dialect.name == "sqlite":
                conn.execute(text("PRAGMA query_only = ON;"))

            # 1. Resolve Person
            try:
                person_stmt = text(
                    "SELECT id, owner_user_id, is_default "
                    "FROM person_profiles WHERE id = :person_id"
                )
                person_row = (
                    conn.execute(person_stmt, {"person_id": str(uuid_obj)}).mappings().first()
                )
            except Exception as exc:
                raise LegacySchemaIncompatibleError(
                    "Failed to query person_profiles table"
                ) from exc

            if person_row is None:
                raise LegacyPersonNotFoundError(str(uuid_obj))

            owner_user_id = person_row["owner_user_id"]
            is_default = bool(person_row["is_default"])

            # 2. Query health metrics
            try:
                if is_default:
                    metrics_stmt = text(
                        "SELECT id, recorded_at, systolic_bp, diastolic_bp, heart_rate, "
                        "steps, weight_kg, blood_glucose, sleep_hours, note "
                        "FROM health_metrics "
                        "WHERE user_id = :owner_user_id "
                        "  AND (subject_profile_id = :person_id OR subject_profile_id IS NULL) "
                        "ORDER BY recorded_at ASC, id ASC"
                    )
                else:
                    metrics_stmt = text(
                        "SELECT id, recorded_at, systolic_bp, diastolic_bp, heart_rate, "
                        "steps, weight_kg, blood_glucose, sleep_hours, note "
                        "FROM health_metrics "
                        "WHERE user_id = :owner_user_id "
                        "  AND subject_profile_id = :person_id "
                        "ORDER BY recorded_at ASC, id ASC"
                    )

                params = {"owner_user_id": owner_user_id, "person_id": person_row["id"]}
                metric_rows = conn.execute(metrics_stmt, params).mappings().all()
            except Exception as exc:
                raise LegacySchemaIncompatibleError("Failed to query health_metrics table") from exc
    except LegacyExportError:
        raise
    except Exception as exc:
        raise LegacyDatabaseError(f"Database error: {exc.__class__.__name__}") from exc
    finally:
        engine.dispose()

    if len(metric_rows) > MAX_CSV_ROWS:
        raise LegacyExportCompatibilityError(
            code="TOO_MANY_ROWS",
            row_number=MAX_CSV_ROWS + 1,
            field=None,
        )

    headers = [
        "recorded_at",
        "systolic_bp_mm_hg",
        "diastolic_bp_mm_hg",
        "heart_rate_bpm",
        "steps",
        "weight_kg",
        "blood_glucose_mg_dl",
        "sleep_hours",
        "note",
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, lineterminator="\n")
    writer.writeheader()

    for r in metric_rows:
        writer.writerow(
            {
                "recorded_at": _normalize_datetime_str(r["recorded_at"]),
                "systolic_bp_mm_hg": _format_int_value(r["systolic_bp"]),
                "diastolic_bp_mm_hg": _format_int_value(r["diastolic_bp"]),
                "heart_rate_bpm": _format_int_value(r["heart_rate"]),
                "steps": _format_int_value(r["steps"]),
                "weight_kg": _format_decimal_value(r["weight_kg"]),
                "blood_glucose_mg_dl": _format_decimal_value(r["blood_glucose"]),
                "sleep_hours": _format_decimal_value(r["sleep_hours"]),
                "note": _format_str_value(r["note"]),
            }
        )

    csv_bytes = buf.getvalue().encode("utf-8")
    if len(csv_bytes) > MAX_CSV_BYTES:
        raise LegacyExportCompatibilityError(
            code="PAYLOAD_TOO_LARGE",
            row_number=None,
            field=None,
        )

    try:
        parsed_rows = parse_health_metric_rows(csv_bytes)
    except HealthMetricCsvImportValidationError as exc:
        raise LegacyExportCompatibilityError(
            code=exc.code,
            row_number=exc.row_number,
            field=exc.field,
        ) from exc

    if len(parsed_rows) != len(metric_rows):
        raise LegacyExportCompatibilityError(
            code="ROW_COUNT_MISMATCH",
            row_number=None,
            field=None,
        )

    for idx, (legacy_row, parsed_row) in enumerate(
        zip(metric_rows, parsed_rows, strict=True), start=1
    ):
        leg_dt = legacy_row["recorded_at"]
        if isinstance(leg_dt, str):
            leg_dt = datetime.fromisoformat(
                leg_dt.replace("Z", "+00:00") if leg_dt.endswith("Z") else leg_dt
            )
        if leg_dt.tzinfo is not None and leg_dt.tzinfo.utcoffset(leg_dt) is not None:
            leg_dt_utc = leg_dt.astimezone(UTC)
        else:
            leg_dt_utc = leg_dt.replace(tzinfo=UTC)
        if parsed_row.recorded_at != leg_dt_utc:
            raise LegacyExportCompatibilityError(
                code="ROUND_TRIP_MISMATCH",
                row_number=idx,
                field="recorded_at",
            )

        if (legacy_row["systolic_bp"] is None and parsed_row.systolic_bp_mm_hg is not None) or (
            legacy_row["systolic_bp"] is not None
            and parsed_row.systolic_bp_mm_hg != int(legacy_row["systolic_bp"])
        ):
            raise LegacyExportCompatibilityError(
                code="ROUND_TRIP_MISMATCH",
                row_number=idx,
                field="systolic_bp_mm_hg",
            )

        if (legacy_row["diastolic_bp"] is None and parsed_row.diastolic_bp_mm_hg is not None) or (
            legacy_row["diastolic_bp"] is not None
            and parsed_row.diastolic_bp_mm_hg != int(legacy_row["diastolic_bp"])
        ):
            raise LegacyExportCompatibilityError(
                code="ROUND_TRIP_MISMATCH",
                row_number=idx,
                field="diastolic_bp_mm_hg",
            )

        if (legacy_row["heart_rate"] is None and parsed_row.heart_rate_bpm is not None) or (
            legacy_row["heart_rate"] is not None and parsed_rate != int(legacy_row["heart_rate"])
            if (parsed_rate := parsed_row.heart_rate_bpm) is not None
            else False
        ):
            raise LegacyExportCompatibilityError(
                code="ROUND_TRIP_MISMATCH",
                row_number=idx,
                field="heart_rate_bpm",
            )

        if (legacy_row["steps"] is None and parsed_row.steps is not None) or (
            legacy_row["steps"] is not None and parsed_row.steps != int(legacy_row["steps"])
        ):
            raise LegacyExportCompatibilityError(
                code="ROUND_TRIP_MISMATCH",
                row_number=idx,
                field="steps",
            )

        leg_weight = legacy_row["weight_kg"]
        if leg_weight is None and parsed_row.weight_kg is not None:
            raise LegacyExportCompatibilityError(
                code="ROUND_TRIP_MISMATCH",
                row_number=idx,
                field="weight_kg",
            )
        if leg_weight is not None:
            if parsed_row.weight_kg is None or Decimal(str(leg_weight)) != parsed_row.weight_kg:
                raise LegacyExportCompatibilityError(
                    code="ROUND_TRIP_MISMATCH",
                    row_number=idx,
                    field="weight_kg",
                )

        leg_glucose = legacy_row["blood_glucose"]
        if leg_glucose is None and parsed_row.blood_glucose_mg_dl is not None:
            raise LegacyExportCompatibilityError(
                code="ROUND_TRIP_MISMATCH",
                row_number=idx,
                field="blood_glucose_mg_dl",
            )
        if leg_glucose is not None:
            if (
                parsed_row.blood_glucose_mg_dl is None
                or Decimal(str(leg_glucose)) != parsed_row.blood_glucose_mg_dl
            ):
                raise LegacyExportCompatibilityError(
                    code="ROUND_TRIP_MISMATCH",
                    row_number=idx,
                    field="blood_glucose_mg_dl",
                )

        leg_sleep = legacy_row["sleep_hours"]
        if leg_sleep is None and parsed_row.sleep_hours is not None:
            raise LegacyExportCompatibilityError(
                code="ROUND_TRIP_MISMATCH",
                row_number=idx,
                field="sleep_hours",
            )
        if leg_sleep is not None:
            if parsed_row.sleep_hours is None or Decimal(str(leg_sleep)) != parsed_row.sleep_hours:
                raise LegacyExportCompatibilityError(
                    code="ROUND_TRIP_MISMATCH",
                    row_number=idx,
                    field="sleep_hours",
                )

        leg_note = legacy_row["note"]
        leg_note_norm = leg_note if (leg_note is not None and leg_note != "") else None
        if leg_note_norm != parsed_row.note:
            raise LegacyExportCompatibilityError(
                code="ROUND_TRIP_MISMATCH",
                row_number=idx,
                field="note",
            )

    return LegacyExportResult(total_rows=len(metric_rows), csv_bytes=csv_bytes)


def export_legacy_health_metrics_to_file(
    legacy_database_url: str,
    legacy_person_id: uuid.UUID | str,
    output_path: Path | str,
) -> LegacyExportResult:
    dest = Path(output_path).resolve()
    temp_file: Path | None = None
    try:
        result = export_legacy_health_metrics_csv(legacy_database_url, legacy_person_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp_file = dest.parent / f".tmp_{dest.name}_{uuid.uuid4().hex}"
        temp_file.write_bytes(result.csv_bytes)
        os.replace(temp_file, dest)
        temp_file = None
        return result
    except Exception:
        if temp_file is not None and temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        raise
