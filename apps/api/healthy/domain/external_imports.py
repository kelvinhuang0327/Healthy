from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from healthy.domain import metrics as metrics_domain

MAX_CSV_BYTES = 1_048_576
MAX_CSV_ROWS = 5000

SOURCE_TYPE_MANUAL = "manual"
SOURCE_TYPE_EXTERNAL_CSV = "external_csv"

MANDATORY_HEADER = "recorded_at"
SUPPORTED_METRIC_VALUE_HEADERS = {
    "systolic_bp_mm_hg",
    "diastolic_bp_mm_hg",
    "heart_rate_bpm",
    "steps",
    "weight_kg",
    "blood_glucose_mg_dl",
    "sleep_hours",
}
SUPPORTED_OPTIONAL_HEADERS = {
    *SUPPORTED_METRIC_VALUE_HEADERS,
    "note",
}
ALL_SUPPORTED_HEADERS = {
    MANDATORY_HEADER,
    *SUPPORTED_OPTIONAL_HEADERS,
}


class HealthMetricCsvImportError(ValueError):
    """Raised when CSV-level parsing or validation fails."""


class HealthMetricCsvImportValidationError(HealthMetricCsvImportError):
    def __init__(self, *, row_number: int | None, field: str | None, code: str) -> None:
        super().__init__(row_number, field, code)
        self.row_number = row_number
        self.field = field
        self.code = code

    def detail(self) -> dict[str, object]:
        return {
            "row": self.row_number,
            "field": self.field,
            "code": self.code,
        }

    def __str__(self) -> str:
        if self.row_number is None:
            return f"Invalid CSV payload: code={self.code}"
        if self.field is None:
            return f"Invalid CSV payload at row {self.row_number}: code={self.code}"
        return f"Invalid CSV payload at row {self.row_number}, field {self.field}: code={self.code}"


@dataclass(frozen=True, slots=True)
class ParsedHealthMetricRow:
    recorded_at: datetime
    systolic_bp_mm_hg: int | None
    diastolic_bp_mm_hg: int | None
    heart_rate_bpm: int | None
    steps: int | None
    weight_kg: Decimal | None
    blood_glucose_mg_dl: Decimal | None
    sleep_hours: Decimal | None
    note: str | None
    source_record_fingerprint: str


@dataclass(frozen=True, slots=True)
class ExternalMetricCsvImportSummary:
    source_type: str
    total_rows: int
    imported_count: int
    duplicate_count: int


def _normalize_csv_value(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _validate_exact_utf8(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HealthMetricCsvImportValidationError(
            row_number=None,
            field="raw_payload",
            code="INVALID_UTF8",
        ) from exc


def _normalize_header(header: str | None) -> str:
    return _normalize_csv_value(header).lower() if header is not None else ""


def _validation_error(*, row_number: int | None, field: str | None, code: str) -> None:
    raise HealthMetricCsvImportValidationError(
        row_number=row_number,
        field=field,
        code=code,
    )


def _decimal_precision_is_valid(value: Decimal, max_digits: int, decimal_places: int) -> bool:
    if value.is_nan() or value.is_infinite():
        return False
    as_tuple = value.copy_abs().normalize().as_tuple()
    exponent = as_tuple.exponent
    if not isinstance(exponent, int):
        return False
    digits = as_tuple.digits or (0,)

    if exponent >= 0:
        integer_digits = len(digits) + exponent
        fractional_digits = 0
    else:
        fractional_digits = -exponent
        integer_digits = max(len(digits) - fractional_digits, 0)

    return integer_digits + fractional_digits <= max_digits and fractional_digits <= decimal_places


def _parse_int(
    raw: str,
    *,
    row_number: int,
    field: str,
    minimum: int | None,
    maximum: int | None,
) -> int | None:
    if raw == "":
        return None
    if raw in {"+", "-"}:
        _validation_error(row_number=row_number, field=field, code="INVALID_INTEGER")
    try:
        value = int(raw)
    except ValueError as exc:
        _validation_error(row_number=row_number, field=field, code="INVALID_INTEGER")
        raise exc
    if minimum is not None and value < minimum:
        _validation_error(row_number=row_number, field=field, code="OUT_OF_RANGE")
    if maximum is not None and value > maximum:
        _validation_error(row_number=row_number, field=field, code="OUT_OF_RANGE")
    return value


def _parse_decimal(
    raw: str,
    *,
    row_number: int,
    field: str,
    minimum: Decimal | None,
    maximum: Decimal | None,
    max_digits: int,
    decimal_places: int,
) -> Decimal | None:
    if raw == "":
        return None
    try:
        value = Decimal(raw)
    except Exception as exc:
        _validation_error(row_number=row_number, field=field, code="INVALID_DECIMAL")
        raise exc

    if value.is_nan() or value.is_infinite():
        _validation_error(row_number=row_number, field=field, code="INVALID_DECIMAL")

    if minimum is not None and value < minimum:
        _validation_error(row_number=row_number, field=field, code="OUT_OF_RANGE")
    if maximum is not None and value > maximum:
        _validation_error(row_number=row_number, field=field, code="OUT_OF_RANGE")

    if not _decimal_precision_is_valid(
        value,
        max_digits=max_digits,
        decimal_places=decimal_places,
    ):
        _validation_error(row_number=row_number, field=field, code="INVALID_PRECISION")

    return value


def _parse_recorded_at(raw: str, *, row_number: int) -> datetime:
    if raw == "":
        _validation_error(row_number=row_number, field=MANDATORY_HEADER, code="MISSING_RECORDED_AT")
    normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        value = datetime.fromisoformat(normalized)
    except ValueError as exc:
        _validation_error(row_number=row_number, field=MANDATORY_HEADER, code="INVALID_TIMESTAMP")
        raise exc

    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        _validation_error(
            row_number=row_number, field=MANDATORY_HEADER, code="TIMESTAMP_REQUIRED_TZ"
        )

    normalized_utc = value.astimezone(UTC)
    if normalized_utc > datetime.now(UTC) + metrics_domain.RECORDED_AT_MAX_FUTURE_SKEW:
        _validation_error(
            row_number=row_number, field=MANDATORY_HEADER, code="TIMESTAMP_TOO_FUTURE"
        )
    return normalized_utc


def build_row_fingerprint(
    *,
    recorded_at: datetime,
    systolic_bp_mm_hg: int | None,
    diastolic_bp_mm_hg: int | None,
    heart_rate_bpm: int | None,
    steps: int | None,
    weight_kg: Decimal | None,
    blood_glucose_mg_dl: Decimal | None,
    sleep_hours: Decimal | None,
    note: str | None,
) -> str:
    canonical_payload = {
        "blood_glucose_mg_dl": str(blood_glucose_mg_dl)
        if blood_glucose_mg_dl is not None
        else None,
        "diastolic_bp_mm_hg": diastolic_bp_mm_hg,
        "heart_rate_bpm": heart_rate_bpm,
        "note": note,
        "recorded_at": recorded_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "sleep_hours": str(sleep_hours) if sleep_hours is not None else None,
        "steps": steps,
        "systolic_bp_mm_hg": systolic_bp_mm_hg,
        "weight_kg": str(weight_kg) if weight_kg is not None else None,
    }
    canonical = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_health_metric_rows(payload: bytes) -> list[ParsedHealthMetricRow]:
    if len(payload) > MAX_CSV_BYTES:
        _validation_error(row_number=None, field=None, code="PAYLOAD_TOO_LARGE")

    text = _validate_exact_utf8(payload)
    if not text.strip():
        _validation_error(row_number=None, field=None, code="INVALID_HEADER")

    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        _validation_error(row_number=None, field=None, code="INVALID_HEADER")
    assert reader.fieldnames is not None
    headers = [_normalize_header(header) for header in reader.fieldnames]

    if not headers or any(header == "" for header in headers):
        _validation_error(row_number=None, field=None, code="INVALID_HEADER")

    if len(headers) != len(set(headers)):
        _validation_error(row_number=None, field="header", code="DUPLICATE_HEADER")

    if MANDATORY_HEADER not in headers:
        _validation_error(row_number=None, field=MANDATORY_HEADER, code="MISSING_REQUIRED_HEADER")

    unknown = [header for header in headers if header not in ALL_SUPPORTED_HEADERS]
    if unknown:
        _validation_error(row_number=None, field="header", code="UNKNOWN_HEADER")

    metric_header_count = sum(1 for header in headers if header in SUPPORTED_METRIC_VALUE_HEADERS)
    if metric_header_count == 0:
        _validation_error(row_number=None, field=None, code="NO_METRIC_HEADER")

    rows: list[ParsedHealthMetricRow] = []
    for row_number, raw_row in enumerate(reader, start=1):
        if row_number > MAX_CSV_ROWS:
            _validation_error(row_number=row_number, field=None, code="TOO_MANY_ROWS")

        normalized_row: dict[str, str] = {}
        for raw_header, raw_value in raw_row.items():
            header = _normalize_header(raw_header)
            normalized_row[header] = _normalize_csv_value(raw_value)

        for header in headers:
            normalized_row.setdefault(header, "")

        recorded_at = _parse_recorded_at(
            normalized_row[MANDATORY_HEADER],
            row_number=row_number,
        )
        systolic = _parse_int(
            normalized_row.get("systolic_bp_mm_hg", ""),
            row_number=row_number,
            field="systolic_bp_mm_hg",
            minimum=metrics_domain.SYSTOLIC_BP_MM_HG_MIN,
            maximum=metrics_domain.SYSTOLIC_BP_MM_HG_MAX,
        )
        diastolic = _parse_int(
            normalized_row.get("diastolic_bp_mm_hg", ""),
            row_number=row_number,
            field="diastolic_bp_mm_hg",
            minimum=metrics_domain.DIASTOLIC_BP_MM_HG_MIN,
            maximum=metrics_domain.DIASTOLIC_BP_MM_HG_MAX,
        )
        heart_rate = _parse_int(
            normalized_row.get("heart_rate_bpm", ""),
            row_number=row_number,
            field="heart_rate_bpm",
            minimum=metrics_domain.HEART_RATE_BPM_MIN,
            maximum=metrics_domain.HEART_RATE_BPM_MAX,
        )
        steps = _parse_int(
            normalized_row.get("steps", ""),
            row_number=row_number,
            field="steps",
            minimum=metrics_domain.STEPS_MIN,
            maximum=metrics_domain.STEPS_MAX,
        )
        weight = _parse_decimal(
            normalized_row.get("weight_kg", ""),
            row_number=row_number,
            field="weight_kg",
            minimum=metrics_domain.WEIGHT_KG_MIN,
            maximum=metrics_domain.WEIGHT_KG_MAX,
            max_digits=5,
            decimal_places=metrics_domain.WEIGHT_KG_DECIMAL_PLACES,
        )
        glucose = _parse_decimal(
            normalized_row.get("blood_glucose_mg_dl", ""),
            row_number=row_number,
            field="blood_glucose_mg_dl",
            minimum=metrics_domain.BLOOD_GLUCOSE_MG_DL_MIN,
            maximum=metrics_domain.BLOOD_GLUCOSE_MG_DL_MAX,
            max_digits=5,
            decimal_places=metrics_domain.BLOOD_GLUCOSE_MG_DL_DECIMAL_PLACES,
        )
        sleep_hours = _parse_decimal(
            normalized_row.get("sleep_hours", ""),
            row_number=row_number,
            field="sleep_hours",
            minimum=Decimal("0.00"),
            maximum=Decimal("99.99"),
            max_digits=metrics_domain.SLEEP_HOURS_MAX_DIGITS,
            decimal_places=metrics_domain.SLEEP_HOURS_DECIMAL_PLACES,
        )

        note_val = normalized_row.get("note", "")
        note = note_val if note_val else None
        if note is not None and len(note) > metrics_domain.NOTE_MAX_LENGTH:
            _validation_error(row_number=row_number, field="note", code="NOTE_TOO_LONG")

        if not metrics_domain.has_at_least_one_metric_value(
            systolic_bp_mm_hg=systolic,
            diastolic_bp_mm_hg=diastolic,
            heart_rate_bpm=heart_rate,
            steps=steps,
            weight_kg=weight,
            blood_glucose_mg_dl=glucose,
            sleep_hours=sleep_hours,
        ):
            _validation_error(row_number=row_number, field=None, code="BLANK_METRIC_ROW")

        if not metrics_domain.blood_pressure_is_paired(
            systolic_bp_mm_hg=systolic,
            diastolic_bp_mm_hg=diastolic,
        ):
            _validation_error(
                row_number=row_number, field="blood_pressure", code="UNPAIRED_BLOOD_PRESSURE"
            )

        fingerprint = build_row_fingerprint(
            recorded_at=recorded_at,
            systolic_bp_mm_hg=systolic,
            diastolic_bp_mm_hg=diastolic,
            heart_rate_bpm=heart_rate,
            steps=steps,
            weight_kg=weight,
            blood_glucose_mg_dl=glucose,
            sleep_hours=sleep_hours,
            note=note,
        )
        row = ParsedHealthMetricRow(
            recorded_at=recorded_at,
            systolic_bp_mm_hg=systolic,
            diastolic_bp_mm_hg=diastolic,
            heart_rate_bpm=heart_rate,
            steps=steps,
            weight_kg=weight,
            blood_glucose_mg_dl=glucose,
            sleep_hours=sleep_hours,
            note=note,
            source_record_fingerprint=fingerprint,
        )
        rows.append(row)

    return rows


def build_import_summary(
    *,
    rows: list[ParsedHealthMetricRow],
    inserted_count: int,
) -> ExternalMetricCsvImportSummary:
    return ExternalMetricCsvImportSummary(
        source_type=SOURCE_TYPE_EXTERNAL_CSV,
        total_rows=len(rows),
        imported_count=inserted_count,
        duplicate_count=max(len(rows) - inserted_count, 0),
    )
