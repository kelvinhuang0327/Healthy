from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from conftest import DATABASE_URL, ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.application.legacy_metric_export import (
    LegacyExportCompatibilityError,
    export_legacy_health_metrics_csv,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


@dataclass(frozen=True, slots=True)
class LegacyMetricFixture:
    id: uuid.UUID
    user_id: uuid.UUID
    subject_profile_id: uuid.UUID | None
    recorded_at: datetime
    systolic_bp: int | None = None
    diastolic_bp: int | None = None
    heart_rate: int | None = None
    blood_glucose: Decimal | None = None
    weight_kg: Decimal | None = None
    sleep_hours: Decimal | None = None
    steps: int | None = None
    note: str | None = None


OWNER_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
OWNER_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
DEFAULT_PERSON_A = uuid.UUID("00000000-0000-0000-0000-000000000101")
OTHER_PERSON_A = uuid.UUID("00000000-0000-0000-0000-000000000102")
INCOMPATIBLE_PERSON_A = uuid.UUID("00000000-0000-0000-0000-000000000103")
PERSON_B = uuid.UUID("00000000-0000-0000-0000-000000000201")

DEFAULT_FULL_METRIC = LegacyMetricFixture(
    id=uuid.UUID("00000000-0000-0000-0000-000000000301"),
    user_id=OWNER_A,
    subject_profile_id=DEFAULT_PERSON_A,
    recorded_at=datetime.fromisoformat("2026-08-01T16:30:00+08:00"),
    systolic_bp=125,
    diastolic_bp=82,
    heart_rate=68,
    blood_glucose=Decimal("102.3"),
    weight_kg=Decimal("68.75"),
    sleep_hours=Decimal("8.25"),
    steps=10500,
    note="Default Person full measurement",
)
DEFAULT_NULL_SUBJECT_METRIC = LegacyMetricFixture(
    id=uuid.UUID("00000000-0000-0000-0000-000000000302"),
    user_id=OWNER_A,
    subject_profile_id=None,
    recorded_at=datetime.fromisoformat("2026-08-02T00:15:00-04:00"),
    heart_rate=72,
    steps=5000,
    note="Owner default null-subject metric",
)
DEFAULT_DECIMAL_METRIC = LegacyMetricFixture(
    id=uuid.UUID("00000000-0000-0000-0000-000000000303"),
    user_id=OWNER_A,
    subject_profile_id=DEFAULT_PERSON_A,
    recorded_at=datetime.fromisoformat("2026-08-03T09:00:00Z"),
    blood_glucose=Decimal("95.55"),
    weight_kg=Decimal("70.50"),
    sleep_hours=Decimal("7.50"),
    note="Default Person decimal measurement",
)
OTHER_PERSON_METRIC = LegacyMetricFixture(
    id=uuid.UUID("00000000-0000-0000-0000-000000000304"),
    user_id=OWNER_A,
    subject_profile_id=OTHER_PERSON_A,
    recorded_at=datetime.fromisoformat("2026-08-04T09:00:00Z"),
    systolic_bp=180,
    diastolic_bp=110,
    heart_rate=88,
    steps=2222,
    weight_kg=Decimal("50.00"),
    blood_glucose=Decimal("180.0"),
    sleep_hours=Decimal("4.00"),
    note="Other Person of owner A",
)
FOREIGN_OWNER_METRIC = LegacyMetricFixture(
    id=uuid.UUID("00000000-0000-0000-0000-000000000305"),
    user_id=OWNER_B,
    subject_profile_id=PERSON_B,
    recorded_at=datetime.fromisoformat("2026-08-05T09:00:00Z"),
    heart_rate=66,
    steps=999,
    note="Foreign owner metric",
)
INCOMPATIBLE_METRIC = LegacyMetricFixture(
    id=uuid.UUID("00000000-0000-0000-0000-000000000306"),
    user_id=OWNER_A,
    subject_profile_id=INCOMPATIBLE_PERSON_A,
    recorded_at=datetime.fromisoformat("2026-08-06T09:00:00Z"),
    blood_glucose=Decimal("1000.01"),
    note="Incompatible range metric",
)

PERSON_FIXTURES = (
    (DEFAULT_PERSON_A, OWNER_A, True),
    (OTHER_PERSON_A, OWNER_A, False),
    (INCOMPATIBLE_PERSON_A, OWNER_A, False),
    (PERSON_B, OWNER_B, True),
)
METRIC_FIXTURES = (
    DEFAULT_FULL_METRIC,
    DEFAULT_NULL_SUBJECT_METRIC,
    DEFAULT_DECIMAL_METRIC,
    OTHER_PERSON_METRIC,
    FOREIGN_OWNER_METRIC,
    INCOMPATIBLE_METRIC,
)
EXPECTED_EXPORT_METRICS = (
    DEFAULT_FULL_METRIC,
    DEFAULT_NULL_SUBJECT_METRIC,
    DEFAULT_DECIMAL_METRIC,
)


def _legacy_database_url(schema_name: str) -> str:
    return f"{DATABASE_URL}?options=-csearch_path%3D{schema_name}"


def _create_legacy_fixture(engine: Engine, schema_name: str) -> None:
    with engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema_name}"))  # noqa: S608
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

        for person_id, owner_user_id, is_default in PERSON_FIXTURES:
            connection.execute(  # noqa: S608
                text(
                    f"INSERT INTO {schema_name}.person_profiles "  # noqa: S608
                    "(id, owner_user_id, is_default) "
                    "VALUES (:id, :owner_user_id, :is_default)"
                ),
                {
                    "id": person_id,
                    "owner_user_id": owner_user_id,
                    "is_default": is_default,
                },
            )

        for metric in METRIC_FIXTURES:
            connection.execute(  # noqa: S608
                text(
                    f"INSERT INTO {schema_name}.health_metrics "  # noqa: S608
                    "(id, user_id, subject_profile_id, recorded_at, systolic_bp, "
                    "diastolic_bp, heart_rate, blood_glucose, weight_kg, sleep_hours, steps, note) "
                    "VALUES (:id, :user_id, :subject_profile_id, :recorded_at, :systolic_bp, "
                    ":diastolic_bp, :heart_rate, :blood_glucose, :weight_kg, :sleep_hours, "
                    ":steps, :note)"
                ),
                {
                    "id": metric.id,
                    "user_id": metric.user_id,
                    "subject_profile_id": metric.subject_profile_id,
                    "recorded_at": metric.recorded_at,
                    "systolic_bp": metric.systolic_bp,
                    "diastolic_bp": metric.diastolic_bp,
                    "heart_rate": metric.heart_rate,
                    "blood_glucose": metric.blood_glucose,
                    "weight_kg": metric.weight_kg,
                    "sleep_hours": metric.sleep_hours,
                    "steps": metric.steps,
                    "note": metric.note,
                },
            )


def _snapshot_legacy_fixture(
    engine: Engine,
    schema_name: str,
) -> tuple[tuple[tuple[object, ...], ...], tuple[tuple[object, ...], ...]]:
    with engine.connect() as connection:
        person_rows = tuple(
            tuple(row)
            for row in connection.execute(  # noqa: S608
                text(
                    f"SELECT id, owner_user_id, is_default "  # noqa: S608
                    f"FROM {schema_name}.person_profiles ORDER BY id"
                )
            ).all()
        )
        metric_rows = tuple(
            tuple(row)
            for row in connection.execute(  # noqa: S608
                text(
                    f"SELECT id, user_id, subject_profile_id, recorded_at, systolic_bp, "  # noqa: S608
                    f"diastolic_bp, heart_rate, blood_glucose, weight_kg, sleep_hours, steps, note "
                    f"FROM {schema_name}.health_metrics ORDER BY id"
                )
            ).all()
        )
    return person_rows, metric_rows


def _drop_legacy_schema(engine: Engine, schema_name: str) -> None:
    with engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))  # noqa: S608


def _schema_exists(database_url: str, schema_name: str) -> bool:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return bool(
                connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :schema_name)"),
                    {"schema_name": schema_name},
                )
            )
    finally:
        engine.dispose()


def _default_person_id(client: TestClient) -> str:
    response = client.get("/v1/persons")
    assert response.status_code == 200, response.text
    person = next(person for person in response.json() if person["is_default"])
    return person["id"]


def _import_csv(client: TestClient, person_id: str, csv_bytes: bytes):
    return client.post(
        f"/v1/persons/{person_id}/metrics/imports/csv",
        headers={"Content-Type": "text/csv", **csrf_headers(client)},
        content=csv_bytes,
    )


def _as_utc(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(UTC)


def _as_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _healthy_projection(row: dict[str, object]) -> tuple[object, ...]:
    return (
        _as_utc(str(row["recorded_at"])),
        row["systolic_bp_mm_hg"],
        row["diastolic_bp_mm_hg"],
        row["heart_rate_bpm"],
        row["steps"],
        _as_decimal(row["weight_kg"]),
        _as_decimal(row["blood_glucose_mg_dl"]),
        _as_decimal(row["sleep_hours"]),
        row["note"],
    )


def _expected_projection(metric: LegacyMetricFixture) -> tuple[object, ...]:
    return (
        metric.recorded_at.astimezone(UTC),
        metric.systolic_bp,
        metric.diastolic_bp,
        metric.heart_rate,
        metric.steps,
        metric.weight_kg,
        metric.blood_glucose,
        metric.sleep_hours,
        metric.note,
    )


def test_legacy_metric_bridge_rehearsal_uses_postgres_export_and_http_import(
    client: TestClient,
) -> None:
    admin_engine = create_engine(DATABASE_URL)
    schema_name = f"legacy_rehearsal_{uuid.uuid4().hex}"

    try:
        _create_legacy_fixture(admin_engine, schema_name)
        legacy_snapshot_before = _snapshot_legacy_fixture(admin_engine, schema_name)
        export_result = export_legacy_health_metrics_csv(
            _legacy_database_url(schema_name),
            DEFAULT_PERSON_A,
        )

        assert export_result.total_rows == len(EXPECTED_EXPORT_METRICS) == 3
        assert _snapshot_legacy_fixture(admin_engine, schema_name) == legacy_snapshot_before

        assert register(client, email="legacy-rehearsal-owner-a@example.com").status_code == 201
        healthy_person_id = _default_person_id(client)

        first_import = _import_csv(client, healthy_person_id, export_result.csv_bytes)
        assert first_import.status_code == 200, first_import.text
        assert first_import.json() == {
            "source_type": "external_csv",
            "total_rows": export_result.total_rows,
            "imported_count": export_result.total_rows,
            "duplicate_count": 0,
        }

        metrics_response = client.get(f"/v1/persons/{healthy_person_id}/metrics")
        assert metrics_response.status_code == 200, metrics_response.text
        first_metrics = metrics_response.json()
        assert len(first_metrics) == export_result.total_rows
        assert all(metric["source_type"] == "external_csv" for metric in first_metrics)
        assert {_healthy_projection(metric) for metric in first_metrics} == {
            _expected_projection(metric) for metric in EXPECTED_EXPORT_METRICS
        }

        first_metric_ids = [metric["id"] for metric in first_metrics]

        history_response = client.get(f"/v1/persons/{healthy_person_id}/history")
        assert history_response.status_code == 200, history_response.text
        metric_history = [item for item in history_response.json() if item["kind"] == "metric"]
        assert {item["id"] for item in metric_history} == set(first_metric_ids)
        assert {item["source"]["id"] for item in metric_history} == set(first_metric_ids)
        assert all(item["source"]["type"] == "metric" for item in metric_history)

        analytics_response = client.get(f"/v1/persons/{healthy_person_id}/analytics?days=30")
        assert analytics_response.status_code == 200, analytics_response.text
        analytics = analytics_response.json()
        assert analytics["period_days"] == 30
        analytics_points = {item["metric"]: item["points"] for item in analytics["summaries"]}
        assert analytics_points == {
            "systolic_bp_mm_hg": 1,
            "diastolic_bp_mm_hg": 1,
            "heart_rate_bpm": 2,
            "steps": 2,
            "weight_kg": 2,
            "blood_glucose_mg_dl": 2,
            "sleep_hours": 2,
        }

        second_import = _import_csv(client, healthy_person_id, export_result.csv_bytes)
        assert second_import.status_code == 200, second_import.text
        assert second_import.json() == {
            "source_type": "external_csv",
            "total_rows": export_result.total_rows,
            "imported_count": 0,
            "duplicate_count": export_result.total_rows,
        }

        second_metrics_response = client.get(f"/v1/persons/{healthy_person_id}/metrics")
        assert second_metrics_response.status_code == 200, second_metrics_response.text
        second_metrics = second_metrics_response.json()
        assert len(second_metrics) == len(first_metrics)
        assert [metric["id"] for metric in second_metrics] == first_metric_ids
        assert {_healthy_projection(metric) for metric in second_metrics} == {
            _healthy_projection(metric) for metric in first_metrics
        }

        with TestClient(client.app, base_url=ORIGIN) as failure_client:
            assert (
                register(
                    failure_client,
                    email="legacy-rehearsal-incompatible@example.com",
                ).status_code
                == 201
            )
            failure_person_id = _default_person_id(failure_client)
            assert failure_client.get(f"/v1/persons/{failure_person_id}/metrics").json() == []

            import_attempted = False
            with pytest.raises(LegacyExportCompatibilityError) as error_info:
                incompatible_export = export_legacy_health_metrics_csv(
                    _legacy_database_url(schema_name),
                    INCOMPATIBLE_PERSON_A,
                )
                import_attempted = True
                _import_csv(failure_client, failure_person_id, incompatible_export.csv_bytes)

            assert import_attempted is False
            assert error_info.value.code == "OUT_OF_RANGE"
            assert error_info.value.row_number == 1
            assert error_info.value.field == "blood_glucose_mg_dl"
            assert failure_client.get(f"/v1/persons/{failure_person_id}/metrics").json() == []

        assert _snapshot_legacy_fixture(admin_engine, schema_name) == legacy_snapshot_before
    finally:
        _drop_legacy_schema(admin_engine, schema_name)
        admin_engine.dispose()

    assert _schema_exists(DATABASE_URL, schema_name) is False
