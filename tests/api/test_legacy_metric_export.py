from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from healthy.application.legacy_metric_export import (
    LegacyExportCompatibilityError,
    LegacyPersonNotFoundError,
    LegacySchemaIncompatibleError,
    export_legacy_health_metrics_csv,
    export_legacy_health_metrics_to_file,
)
from healthy.domain.external_imports import parse_health_metric_rows


def _setup_legacy_sqlite_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE person_profiles (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            relationship TEXT NOT NULL DEFAULT 'self',
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE health_metrics (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            subject_profile_id TEXT,
            recorded_at TEXT NOT NULL,
            systolic_bp INTEGER,
            diastolic_bp INTEGER,
            heart_rate INTEGER,
            blood_glucose TEXT,
            weight_kg TEXT,
            sleep_hours TEXT,
            steps INTEGER,
            note TEXT,
            source TEXT DEFAULT 'manual',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (subject_profile_id) REFERENCES person_profiles(id)
        );
        """
    )
    conn.commit()
    return conn


def _insert_person(
    conn: sqlite3.Connection,
    person_id: str,
    owner_user_id: str,
    display_name: str,
    is_default: int,
) -> None:
    conn.execute(
        "INSERT INTO person_profiles (id, owner_user_id, display_name, is_default) "
        "VALUES (?, ?, ?, ?)",
        (person_id, owner_user_id, display_name, is_default),
    )


def test_default_person_includes_explicit_and_null_subject_rows_and_excludes_foreign(
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)

    user_a_id = str(uuid.uuid4())
    user_b_id = str(uuid.uuid4())
    default_person_a_id = str(uuid.uuid4())
    other_person_a_id = str(uuid.uuid4())
    default_person_b_id = str(uuid.uuid4())

    _insert_person(conn, default_person_a_id, user_a_id, "User A Default", 1)
    _insert_person(conn, other_person_a_id, user_a_id, "User A Child", 0)
    _insert_person(conn, default_person_b_id, user_b_id, "User B Default", 1)

    # Metric 1: User A explicit default person (Included)
    m1_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO health_metrics (id, user_id, subject_profile_id, recorded_at, heart_rate) "
        "VALUES (?, ?, ?, ?, ?)",
        (m1_id, user_a_id, default_person_a_id, "2026-08-01T08:00:00Z", 70),
    )

    # Metric 2: User A NULL subject_profile_id historical row (Included because default person)
    m2_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO health_metrics (id, user_id, subject_profile_id, recorded_at, heart_rate) "
        "VALUES (?, ?, ?, ?, ?)",
        (m2_id, user_a_id, None, "2026-08-01T09:00:00Z", 72),
    )

    # Metric 3: User A other person row (Excluded)
    m3_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO health_metrics (id, user_id, subject_profile_id, recorded_at, heart_rate) "
        "VALUES (?, ?, ?, ?, ?)",
        (m3_id, user_a_id, other_person_a_id, "2026-08-01T10:00:00Z", 75),
    )

    # Metric 4: User B default person row (Excluded - foreign user)
    m4_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO health_metrics (id, user_id, subject_profile_id, recorded_at, heart_rate) "
        "VALUES (?, ?, ?, ?, ?)",
        (m4_id, user_b_id, default_person_b_id, "2026-08-01T11:00:00Z", 80),
    )

    # Metric 5: User B NULL subject row (Excluded - foreign user)
    m5_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO health_metrics (id, user_id, subject_profile_id, recorded_at, heart_rate) "
        "VALUES (?, ?, ?, ?, ?)",
        (m5_id, user_b_id, None, "2026-08-01T12:00:00Z", 82),
    )

    conn.commit()
    conn.close()

    db_url = f"sqlite:///{db_file}"
    result = export_legacy_health_metrics_csv(db_url, default_person_a_id)
    assert result.total_rows == 2

    parsed = parse_health_metric_rows(result.csv_bytes)
    assert len(parsed) == 2
    assert [p.heart_rate_bpm for p in parsed] == [70, 72]


def test_non_default_person_includes_only_own_rows_and_excludes_null_subject(
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)

    user_a_id = str(uuid.uuid4())
    default_person_a_id = str(uuid.uuid4())
    non_default_person_a_id = str(uuid.uuid4())

    _insert_person(conn, default_person_a_id, user_a_id, "User A Default", 1)
    _insert_person(conn, non_default_person_a_id, user_a_id, "User A NonDefault", 0)

    # 1. Non-default person explicit row (Included)
    conn.execute(
        "INSERT INTO health_metrics (id, user_id, subject_profile_id, recorded_at, heart_rate) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), user_a_id, non_default_person_a_id, "2026-08-01T08:00:00Z", 65),
    )

    # 2. NULL subject row (Excluded for non-default person)
    conn.execute(
        "INSERT INTO health_metrics (id, user_id, subject_profile_id, recorded_at, heart_rate) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), user_a_id, None, "2026-08-01T09:00:00Z", 70),
    )

    # 3. Default person row (Excluded for non-default person)
    conn.execute(
        "INSERT INTO health_metrics (id, user_id, subject_profile_id, recorded_at, heart_rate) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), user_a_id, default_person_a_id, "2026-08-01T10:00:00Z", 75),
    )

    conn.commit()
    conn.close()

    db_url = f"sqlite:///{db_file}"
    result = export_legacy_health_metrics_csv(db_url, non_default_person_a_id)
    assert result.total_rows == 1

    parsed = parse_health_metric_rows(result.csv_bytes)
    assert len(parsed) == 1
    assert parsed[0].heart_rate_bpm == 65


def test_exact_mapping_headers_utc_normalization_and_stable_ordering(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)

    user_id = str(uuid.uuid4())
    person_id = str(uuid.uuid4())

    _insert_person(conn, person_id, user_id, "User Default", 1)

    # Insert out-of-order timestamps and tie-breaker IDs
    id_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    id_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    id_c = "cccccccc-cccc-cccc-cccc-cccccccccccc"

    # Same timestamp for id_b and id_a to test stable ordering by id ASC
    insert_sql = (
        "INSERT INTO health_metrics ("
        "  id, user_id, subject_profile_id, recorded_at,"
        "  systolic_bp, diastolic_bp, heart_rate, steps,"
        "  weight_kg, blood_glucose, sleep_hours, note"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )

    conn.execute(
        insert_sql,
        (
            id_b,
            user_id,
            person_id,
            "2026-08-01T16:00:00+08:00",
            120,
            80,
            72,
            5000,
            "70.50",
            "95.5",
            "7.50",
            "Second row tie-break B",
        ),
    )
    conn.execute(
        insert_sql,
        (
            id_a,
            user_id,
            person_id,
            "2026-08-01T16:00:00+08:00",
            122,
            82,
            74,
            5100,
            "70.60",
            "96.0",
            "7.60",
            "First row tie-break A",
        ),
    )
    conn.execute(
        insert_sql,
        (
            id_c,
            user_id,
            person_id,
            "2026-08-01T18:00:00+08:00",
            118,
            78,
            70,
            6000,
            "70.40",
            "94.0",
            "8.00",
            "Third row later timestamp",
        ),
    )

    conn.commit()
    conn.close()

    db_url = f"sqlite:///{db_file}"
    result = export_legacy_health_metrics_csv(db_url, person_id)
    assert result.total_rows == 3

    csv_text = result.csv_bytes.decode("utf-8")
    lines = csv_text.strip().split("\n")
    expected_header = (
        "recorded_at,systolic_bp_mm_hg,diastolic_bp_mm_hg,heart_rate_bpm,"
        "steps,weight_kg,blood_glucose_mg_dl,sleep_hours,note"
    )
    assert lines[0] == expected_header

    # 16:00 +08:00 -> 08:00:00Z
    assert lines[1].startswith("2026-08-01T08:00:00Z,122,82,74,5100,70.60,96.0,7.60")
    assert lines[2].startswith("2026-08-01T08:00:00Z,120,80,72,5000,70.50,95.5,7.50")
    # 18:00 +08:00 -> 10:00:00Z
    assert lines[3].startswith("2026-08-01T10:00:00Z,118,78,70,6000,70.40,94.0,8.00")

    # Round-trip parse
    parsed = parse_health_metric_rows(result.csv_bytes)
    assert len(parsed) == 3
    assert parsed[0].note == "First row tie-break A"
    assert parsed[1].note == "Second row tie-break B"
    assert parsed[2].note == "Third row later timestamp"


def test_round_trip_compatibility_matches_legacy_values_exactly(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)

    user_id = str(uuid.uuid4())
    person_id = str(uuid.uuid4())

    _insert_person(conn, person_id, user_id, "User Default", 1)

    conn.execute(
        """
        INSERT INTO health_metrics (
            id, user_id, subject_profile_id, recorded_at,
            systolic_bp, diastolic_bp, heart_rate, steps,
            weight_kg, blood_glucose, sleep_hours, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            user_id,
            person_id,
            "2026-08-01T10:00:00Z",
            125,
            85,
            75,
            10000,
            "68.50",
            "105.5",
            "8.25",
            "Exact match note",
        ),
    )
    conn.commit()
    conn.close()

    db_url = f"sqlite:///{db_file}"
    result = export_legacy_health_metrics_csv(db_url, person_id)
    assert result.total_rows == 1

    parsed = parse_health_metric_rows(result.csv_bytes)
    assert len(parsed) == 1
    row = parsed[0]
    assert row.recorded_at == datetime(2026, 8, 1, 10, 0, 0, tzinfo=UTC)
    assert row.systolic_bp_mm_hg == 125
    assert row.diastolic_bp_mm_hg == 85
    assert row.heart_rate_bpm == 75
    assert row.steps == 10000
    assert row.weight_kg == Decimal("68.50")
    assert row.blood_glucose_mg_dl == Decimal("105.5")
    assert row.sleep_hours == Decimal("8.25")
    assert row.note == "Exact match note"


def test_precision_incompatibility_fails_closed_without_output(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)

    user_id = str(uuid.uuid4())
    person_id = str(uuid.uuid4())

    _insert_person(conn, person_id, user_id, "User Default", 1)

    # 95.55 has 2 decimal places, exceeding Healthy's 1 decimal place limit for blood glucose
    conn.execute(
        "INSERT INTO health_metrics ("
        "  id, user_id, subject_profile_id, recorded_at, blood_glucose"
        ") VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, person_id, "2026-08-01T10:00:00Z", "95.55"),
    )
    conn.commit()
    conn.close()

    output_csv = tmp_path / "output.csv"
    db_url = f"sqlite:///{db_file}"

    with pytest.raises(LegacyExportCompatibilityError) as exc_info:
        export_legacy_health_metrics_to_file(db_url, person_id, output_csv)

    assert exc_info.value.code == "INVALID_PRECISION"
    assert exc_info.value.field == "blood_glucose_mg_dl"
    assert exc_info.value.row_number == 1
    assert not output_csv.exists()


def test_unpaired_blood_pressure_fails_closed_without_output(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)

    user_id = str(uuid.uuid4())
    person_id = str(uuid.uuid4())

    _insert_person(conn, person_id, user_id, "User Default", 1)

    # Unpaired blood pressure (systolic without diastolic)
    conn.execute(
        "INSERT INTO health_metrics ("
        "  id, user_id, subject_profile_id, recorded_at, systolic_bp, diastolic_bp"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, person_id, "2026-08-01T10:00:00Z", 120, None),
    )
    conn.commit()
    conn.close()

    output_csv = tmp_path / "output.csv"
    db_url = f"sqlite:///{db_file}"

    with pytest.raises(LegacyExportCompatibilityError) as exc_info:
        export_legacy_health_metrics_to_file(db_url, person_id, output_csv)

    assert exc_info.value.code == "UNPAIRED_BLOOD_PRESSURE"
    assert not output_csv.exists()


def test_out_of_range_metric_fails_closed(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)

    user_id = str(uuid.uuid4())
    person_id = str(uuid.uuid4())

    _insert_person(conn, person_id, user_id, "User Default", 1)

    # Heart rate out of range (10 bpm < 20 bpm min)
    conn.execute(
        "INSERT INTO health_metrics ("
        "  id, user_id, subject_profile_id, recorded_at, heart_rate"
        ") VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, person_id, "2026-08-01T10:00:00Z", 10),
    )
    conn.commit()
    conn.close()

    output_csv = tmp_path / "output.csv"
    db_url = f"sqlite:///{db_file}"

    with pytest.raises(LegacyExportCompatibilityError) as exc_info:
        export_legacy_health_metrics_to_file(db_url, person_id, output_csv)

    assert exc_info.value.code == "OUT_OF_RANGE"
    assert exc_info.value.field == "heart_rate_bpm"
    assert not output_csv.exists()


def test_blank_metric_row_fails_closed(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)

    user_id = str(uuid.uuid4())
    person_id = str(uuid.uuid4())

    _insert_person(conn, person_id, user_id, "User Default", 1)

    # Blank metric row (only note, no metric values)
    conn.execute(
        "INSERT INTO health_metrics ("
        "  id, user_id, subject_profile_id, recorded_at, note"
        ") VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, person_id, "2026-08-01T10:00:00Z", "Just a note"),
    )
    conn.commit()
    conn.close()

    output_csv = tmp_path / "output.csv"
    db_url = f"sqlite:///{db_file}"

    with pytest.raises(LegacyExportCompatibilityError) as exc_info:
        export_legacy_health_metrics_to_file(db_url, person_id, output_csv)

    assert exc_info.value.code == "BLANK_METRIC_ROW"
    assert not output_csv.exists()


def test_batch_limits_exceeded_rows_fails_closed(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)

    user_id = str(uuid.uuid4())
    person_id = str(uuid.uuid4())

    _insert_person(conn, person_id, user_id, "User Default", 1)

    # Insert 5001 rows (> 5000)
    items = [
        (
            f"id-{i:05d}",
            user_id,
            person_id,
            f"2026-08-01T{(i % 24):02d}:00:00Z",
            72,
        )
        for i in range(5001)
    ]
    conn.executemany(
        "INSERT INTO health_metrics (id, user_id, subject_profile_id, recorded_at, heart_rate) "
        "VALUES (?, ?, ?, ?, ?)",
        items,
    )
    conn.commit()
    conn.close()

    output_csv = tmp_path / "output.csv"
    db_url = f"sqlite:///{db_file}"

    with pytest.raises(LegacyExportCompatibilityError) as exc_info:
        export_legacy_health_metrics_to_file(db_url, person_id, output_csv)

    assert exc_info.value.code == "TOO_MANY_ROWS"
    assert not output_csv.exists()


def test_batch_limits_exceeded_payload_bytes_fails_closed(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)

    user_id = str(uuid.uuid4())
    person_id = str(uuid.uuid4())

    _insert_person(conn, person_id, user_id, "User Default", 1)

    # Insert 800 rows with 1500-char note (~1.2 MiB > 1 MiB limit)
    big_note = "n" * 1500
    items = [
        (
            f"id-{i:05d}",
            user_id,
            person_id,
            f"2026-08-01T{(i % 24):02d}:00:00Z",
            72,
            big_note,
        )
        for i in range(800)
    ]
    conn.executemany(
        "INSERT INTO health_metrics ("
        "  id, user_id, subject_profile_id, recorded_at, heart_rate, note"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        items,
    )
    conn.commit()
    conn.close()

    output_csv = tmp_path / "output.csv"
    db_url = f"sqlite:///{db_file}"

    with pytest.raises(LegacyExportCompatibilityError) as exc_info:
        export_legacy_health_metrics_to_file(db_url, person_id, output_csv)

    assert exc_info.value.code == "PAYLOAD_TOO_LARGE"
    assert not output_csv.exists()


def test_missing_and_invalid_person_fails_closed(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)
    conn.close()

    db_url = f"sqlite:///{db_file}"

    # Non-existent person
    missing_id = str(uuid.uuid4())
    with pytest.raises(LegacyPersonNotFoundError):
        export_legacy_health_metrics_csv(db_url, missing_id)

    # Invalid UUID format
    with pytest.raises(LegacyExportCompatibilityError) as exc_info:
        export_legacy_health_metrics_csv(db_url, "not-a-valid-uuid")
    assert exc_info.value.code == "INVALID_LEGACY_PERSON_ID"


def test_incompatible_legacy_schema_fails_closed(tmp_path: Path) -> None:
    db_file = tmp_path / "bad_schema.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE wrong_table (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    db_url = f"sqlite:///{db_file}"
    with pytest.raises(LegacySchemaIncompatibleError):
        export_legacy_health_metrics_csv(db_url, str(uuid.uuid4()))


def test_source_database_content_and_count_are_invariant(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)

    user_id = str(uuid.uuid4())
    person_id = str(uuid.uuid4())

    _insert_person(conn, person_id, user_id, "User Default", 1)
    conn.execute(
        "INSERT INTO health_metrics (id, user_id, subject_profile_id, recorded_at, heart_rate) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, person_id, "2026-08-01T08:00:00Z", 72),
    )
    conn.commit()

    def get_db_snapshot() -> tuple[list[tuple], list[tuple]]:
        c = sqlite3.connect(db_file)
        profiles = c.execute("SELECT * FROM person_profiles").fetchall()
        metrics = c.execute("SELECT * FROM health_metrics").fetchall()
        c.close()
        return profiles, metrics

    snapshot_before = get_db_snapshot()

    output_csv = tmp_path / "output.csv"
    db_url = f"sqlite:///{db_file}"
    result = export_legacy_health_metrics_to_file(db_url, person_id, output_csv)
    assert result.total_rows == 1
    assert output_csv.exists()

    snapshot_after = get_db_snapshot()
    assert snapshot_after == snapshot_before


def test_cli_success_and_failure_privacy_and_outputs(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)

    user_id = str(uuid.uuid4())
    person_id = str(uuid.uuid4())

    _insert_person(conn, person_id, user_id, "User Default", 1)
    conn.execute(
        """
        INSERT INTO health_metrics (
            id, user_id, subject_profile_id, recorded_at, systolic_bp, diastolic_bp, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            user_id,
            person_id,
            "2026-08-01T08:00:00Z",
            120,
            80,
            "Confidential note with medical secrets",
        ),
    )
    conn.commit()
    conn.close()

    script_path = Path(__file__).parents[2] / "scripts" / "export_legacy_health_metrics.py"
    db_url = f"sqlite:///{db_file}"
    out_csv = tmp_path / "cli_out.csv"

    # 1. Success execution
    cmd_success = [
        sys.executable,
        str(script_path),
        "--legacy-database-url",
        db_url,
        "--legacy-person-id",
        person_id,
        "--output",
        str(out_csv),
    ]
    proc = subprocess.run(cmd_success, capture_output=True, text=True)  # noqa: S603
    assert proc.returncode == 0
    assert out_csv.exists()

    stdout_json = json.loads(proc.stdout.strip())
    assert stdout_json["status"] == "success"
    assert stdout_json["total_rows"] == 1
    assert stdout_json["output"] == str(out_csv)

    # Ensure stdout/stderr does NOT contain health values or notes
    assert "Confidential" not in proc.stdout
    assert "120" not in proc.stdout
    assert proc.stderr == ""

    # 2. Failure execution: incompatible person (unpaired BP)
    conn = sqlite3.connect(db_file)
    bad_person_id = str(uuid.uuid4())
    _insert_person(conn, bad_person_id, user_id, "Bad Person", 0)
    conn.execute(
        """
        INSERT INTO health_metrics (
            id, user_id, subject_profile_id, recorded_at, systolic_bp, diastolic_bp, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            user_id,
            bad_person_id,
            "2026-08-01T08:00:00Z",
            135,
            None,
            "Secret note",
        ),
    )
    conn.commit()
    conn.close()

    bad_out_csv = tmp_path / "bad_cli_out.csv"
    cmd_fail = [
        sys.executable,
        str(script_path),
        "--legacy-database-url",
        db_url,
        "--legacy-person-id",
        bad_person_id,
        "--output",
        str(bad_out_csv),
    ]
    proc_fail = subprocess.run(cmd_fail, capture_output=True, text=True)  # noqa: S603
    assert proc_fail.returncode == 1
    assert not bad_out_csv.exists()

    stderr_json = json.loads(proc_fail.stderr.strip())
    assert stderr_json["status"] == "error"
    assert stderr_json["code"] == "UNPAIRED_BLOOD_PRESSURE"
    assert stderr_json["row"] == 1
    assert stderr_json["field"] == "blood_pressure"

    # Ensure stderr does NOT contain raw health values or note text
    assert "135" not in proc_fail.stderr
    assert "Secret note" not in proc_fail.stderr


def test_person_with_zero_metrics_exports_empty_csv(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = _setup_legacy_sqlite_db(db_file)

    user_id = str(uuid.uuid4())
    person_id = str(uuid.uuid4())

    _insert_person(conn, person_id, user_id, "User With No Metrics", 1)
    conn.commit()
    conn.close()

    db_url = f"sqlite:///{db_file}"
    out_csv = tmp_path / "zero_metrics.csv"
    result = export_legacy_health_metrics_to_file(db_url, person_id, out_csv)
    assert result.total_rows == 0
    assert out_csv.exists()

    parsed = parse_health_metric_rows(result.csv_bytes)
    assert len(parsed) == 0


def test_postgresql_legacy_source_read_only_and_data_types(tmp_path: Path) -> None:
    from conftest import DATABASE_URL
    from sqlalchemy import create_engine, text

    admin_engine = create_engine(DATABASE_URL)
    schema_name = f"legacy_pg_{uuid.uuid4().hex[:8]}"

    with admin_engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema_name};"))  # noqa: S608
        conn.execute(
            text(
                f"""
                CREATE TABLE {schema_name}.person_profiles (
                    id UUID PRIMARY KEY,
                    owner_user_id UUID NOT NULL,
                    display_name VARCHAR(120) NOT NULL,
                    relationship VARCHAR(30) NOT NULL DEFAULT 'self',
                    is_default BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE {schema_name}.health_metrics (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL,
                    subject_profile_id UUID REFERENCES {schema_name}.person_profiles(id),
                    recorded_at TIMESTAMPTZ NOT NULL,
                    systolic_bp INTEGER,
                    diastolic_bp INTEGER,
                    heart_rate INTEGER,
                    blood_glucose NUMERIC(7,2),
                    weight_kg NUMERIC(5,2),
                    sleep_hours NUMERIC(4,2),
                    steps INTEGER,
                    note TEXT,
                    source VARCHAR(40) DEFAULT 'manual',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """  # noqa: S608
            )
        )

        user_id = str(uuid.uuid4())
        person_id = str(uuid.uuid4())
        conn.execute(
            text(
                f"INSERT INTO {schema_name}.person_profiles "  # noqa: S608
                f"(id, owner_user_id, display_name, is_default) "
                f"VALUES (:id, :owner_user_id, 'PG User', TRUE)"
            ),
            {"id": person_id, "owner_user_id": user_id},
        )
        conn.execute(
            text(
                f"""
                INSERT INTO {schema_name}.health_metrics (
                    id, user_id, subject_profile_id, recorded_at,
                    systolic_bp, diastolic_bp, heart_rate, steps,
                    weight_kg, blood_glucose, sleep_hours, note
                ) VALUES (
                    :id, :user_id, :person_id, '2026-08-01 10:00:00+00',
                    120, 80, 70, 8000,
                    72.50, 95.0, 7.50, 'PG metric note'
                )
                """  # noqa: S608
            ),
            {"id": str(uuid.uuid4()), "user_id": user_id, "person_id": person_id},
        )

    pg_url_with_schema = f"{DATABASE_URL}?options=-csearch_path%3D{schema_name}"

    try:
        out_csv = tmp_path / "pg_export.csv"
        result = export_legacy_health_metrics_to_file(pg_url_with_schema, person_id, out_csv)
        assert result.total_rows == 1
        assert out_csv.exists()

        parsed = parse_health_metric_rows(result.csv_bytes)
        assert len(parsed) == 1
        assert parsed[0].systolic_bp_mm_hg == 120
        assert parsed[0].diastolic_bp_mm_hg == 80
        assert parsed[0].heart_rate_bpm == 70
        assert parsed[0].weight_kg == Decimal("72.50")
        assert parsed[0].blood_glucose_mg_dl == Decimal("95.0")
        assert parsed[0].sleep_hours == Decimal("7.50")
        assert parsed[0].note == "PG metric note"
    finally:
        with admin_engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE;"))  # noqa: S608
        admin_engine.dispose()
