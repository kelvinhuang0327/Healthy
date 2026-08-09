from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

SCHEMA_VERSION_V1 = "healthy.health-report.v1"
HealthReportStatus = Literal["pending", "confirmed"]


class InvalidReportSchemaError(ValueError):
    """Raised when a JSON report does not satisfy healthy.health-report.v1 specification."""


@dataclass(frozen=True, slots=True)
class HealthReportObservation:
    id: uuid.UUID
    report_id: uuid.UUID
    person_id: uuid.UUID
    code: str
    display_name: str
    value_numeric: Decimal | float | None
    value_text: str | None
    unit: str | None
    reference_range: str | None
    observed_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class HealthReport:
    id: uuid.UUID
    person_id: uuid.UUID
    schema_version: str
    source_name: str
    reported_at: datetime
    canonical_sha256: str
    status: HealthReportStatus
    created_at: datetime
    confirmed_at: datetime | None
    observations: tuple[HealthReportObservation, ...]


def parse_iso_datetime(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise InvalidReportSchemaError(f"Field '{field_name}' must be a non-empty ISO 8601 string.")
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise InvalidReportSchemaError(
            f"Field '{field_name}' contains invalid ISO 8601 datetime: '{value}'."
        ) from exc

    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise InvalidReportSchemaError(f"Field '{field_name}' must be timezone-aware.")

    return dt.astimezone(UTC)


def format_canonical_datetime(dt: datetime) -> str:
    utc_dt = dt.astimezone(UTC)
    # Standard ISO format ending in Z
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def canonicalize_and_validate_report_json(data: Any) -> tuple[dict[str, Any], str]:
    """Validates healthy.health-report.v1 schema and produces canonical dict
    and SHA-256 hash."""
    if not isinstance(data, dict):
        raise InvalidReportSchemaError("Report payload must be a JSON object.")

    schema_version = data.get("schema_version")
    if schema_version != SCHEMA_VERSION_V1:
        raise InvalidReportSchemaError(
            f"Unsupported or missing schema_version. Expected '{SCHEMA_VERSION_V1}', "
            f"received '{schema_version}'."
        )

    source_name = data.get("source_name")
    if not isinstance(source_name, str) or not source_name.strip():
        raise InvalidReportSchemaError("Field 'source_name' must be a non-empty string.")
    clean_source_name = source_name.strip()
    if len(clean_source_name) > 128:
        raise InvalidReportSchemaError("Field 'source_name' must not exceed 128 characters.")

    reported_at_dt = parse_iso_datetime(data.get("reported_at"), field_name="reported_at")

    observations_raw = data.get("observations")
    if not isinstance(observations_raw, list) or len(observations_raw) == 0:
        raise InvalidReportSchemaError(
            "Field 'observations' must be a non-empty list of observations."
        )
    if len(observations_raw) > 100:
        raise InvalidReportSchemaError("Field 'observations' must not exceed 100 items per report.")

    canonical_obs: list[dict[str, Any]] = []
    for idx, obs in enumerate(observations_raw):
        if not isinstance(obs, dict):
            raise InvalidReportSchemaError(f"Observation at index {idx} must be an object.")

        code = obs.get("code")
        if not isinstance(code, str) or not code.strip():
            raise InvalidReportSchemaError(f"Observation at index {idx} missing valid 'code'.")
        clean_code = code.strip().upper()
        if len(clean_code) > 64:
            raise InvalidReportSchemaError(
                f"Observation code '{clean_code}' exceeds 64 characters."
            )

        display_name = obs.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise InvalidReportSchemaError(
                f"Observation at index {idx} missing valid 'display_name'."
            )
        clean_display_name = display_name.strip()
        if len(clean_display_name) > 128:
            raise InvalidReportSchemaError("Observation display_name exceeds 128 characters.")

        val_num = obs.get("value_numeric")
        val_text = obs.get("value_text")

        if val_num is None and (
            val_text is None or (isinstance(val_text, str) and not val_text.strip())
        ):
            raise InvalidReportSchemaError(
                f"Observation at index {idx} ('{clean_code}') "
                "must have at least value_numeric or value_text."
            )

        clean_val_num: float | None = None
        if val_num is not None:
            if isinstance(val_num, (int, float)) and not isinstance(val_num, bool):
                clean_val_num = float(val_num)
            elif isinstance(val_num, str) and val_num.strip():
                try:
                    clean_val_num = float(val_num.strip())
                except ValueError as exc:
                    raise InvalidReportSchemaError(
                        f"Observation '{clean_code}' has invalid value_numeric string: '{val_num}'."
                    ) from exc
            else:
                raise InvalidReportSchemaError(
                    f"Observation '{clean_code}' has invalid value_numeric."
                )

        clean_val_text: str | None = None
        if val_text is not None:
            if not isinstance(val_text, str):
                raise InvalidReportSchemaError(
                    f"Observation '{clean_code}' value_text must be a string."
                )
            clean_val_text = val_text.strip()
            if len(clean_val_text) > 2000:
                raise InvalidReportSchemaError(
                    f"Observation '{clean_code}' value_text exceeds 2000 characters."
                )
            if not clean_val_text:
                clean_val_text = None

        unit = obs.get("unit")
        clean_unit: str | None = None
        if unit is not None:
            if not isinstance(unit, str):
                raise InvalidReportSchemaError(f"Observation '{clean_code}' unit must be a string.")
            clean_unit = unit.strip()
            if len(clean_unit) > 32:
                raise InvalidReportSchemaError(
                    f"Observation '{clean_code}' unit exceeds 32 characters."
                )
            if not clean_unit:
                clean_unit = None

        ref_range = obs.get("reference_range")
        clean_ref_range: str | None = None
        if ref_range is not None:
            if not isinstance(ref_range, str):
                raise InvalidReportSchemaError(
                    f"Observation '{clean_code}' reference_range must be a string."
                )
            clean_ref_range = ref_range.strip()
            if len(clean_ref_range) > 128:
                raise InvalidReportSchemaError(
                    f"Observation '{clean_code}' reference_range exceeds 128 characters."
                )
            if not clean_ref_range:
                clean_ref_range = None

        obs_at_raw = obs.get("observed_at")
        if obs_at_raw is not None:
            obs_at_dt = parse_iso_datetime(
                obs_at_raw, field_name=f"observations[{idx}].observed_at"
            )
        else:
            obs_at_dt = reported_at_dt

        canonical_obs_item: dict[str, Any] = {
            "code": clean_code,
            "display_name": clean_display_name,
            "observed_at": format_canonical_datetime(obs_at_dt),
        }
        if clean_val_num is not None:
            canonical_obs_item["value_numeric"] = clean_val_num
        if clean_val_text is not None:
            canonical_obs_item["value_text"] = clean_val_text
        if clean_unit is not None:
            canonical_obs_item["unit"] = clean_unit
        if clean_ref_range is not None:
            canonical_obs_item["reference_range"] = clean_ref_range

        canonical_obs.append(canonical_obs_item)

    # Sort observations deterministically by code, then observed_at, then display_name
    canonical_obs.sort(key=lambda o: (o["code"], o["observed_at"], o["display_name"]))

    canonical_dict: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_V1,
        "source_name": clean_source_name,
        "reported_at": format_canonical_datetime(reported_at_dt),
        "observations": canonical_obs,
    }

    canonical_json_str = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"))
    sha256_hash = hashlib.sha256(canonical_json_str.encode("utf-8")).hexdigest()

    return canonical_dict, sha256_hash
