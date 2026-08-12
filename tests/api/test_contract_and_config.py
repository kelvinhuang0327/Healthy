from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import DATABASE_URL
from healthy.infrastructure.config import Settings
from healthy.infrastructure.database import Database
from healthy.main import create_app
from sqlalchemy import inspect


def test_openapi_has_only_approved_product_endpoints_and_cookie_auth() -> None:
    document = create_app().openapi()
    assert set(document["paths"]) == {
        "/v1/accounts",
        "/v1/sessions",
        "/v1/sessions/current",
        "/v1/session",
        "/v1/persons",
        "/v1/persons/{person_id}",
        "/v1/persons/{person_id}/profile",
        "/v1/persons/{person_id}/metrics",
        "/v1/persons/{person_id}/metrics/{metric_id}",
        "/v1/persons/{person_id}/health-score",
        "/v1/persons/{person_id}/risk-alerts",
        "/v1/persons/{person_id}/symptoms",
        "/v1/persons/{person_id}/symptoms/{symptom_id}",
        "/v1/persons/{person_id}/actions",
        "/v1/persons/{person_id}/actions/{action_id}",
        "/v1/persons/{person_id}/actions/{action_id}/complete",
        "/v1/persons/{person_id}/actions/{action_id}/outcomes",
        "/v1/persons/{person_id}/actions/{action_id}/outcomes/{outcome_id}",
        "/v1/persons/{person_id}/reports",
        "/v1/persons/{person_id}/reports/{report_id}",
        "/v1/persons/{person_id}/reports/{report_id}/confirm",
        "/v1/persons/{person_id}/assistant/today",
        "/v1/persons/{person_id}/history",
        "/v1/persons/{person_id}/analytics",
    }
    operations = {
        (method.upper(), path)
        for path, methods in document["paths"].items()
        for method in methods
        if method in {"get", "post", "patch", "delete"}
    }
    assert operations == {
        ("POST", "/v1/accounts"),
        ("POST", "/v1/sessions"),
        ("DELETE", "/v1/sessions/current"),
        ("GET", "/v1/session"),
        ("GET", "/v1/persons"),
        ("POST", "/v1/persons"),
        ("GET", "/v1/persons/{person_id}"),
        ("PATCH", "/v1/persons/{person_id}/profile"),
        ("POST", "/v1/persons/{person_id}/metrics"),
        ("GET", "/v1/persons/{person_id}/metrics"),
        ("GET", "/v1/persons/{person_id}/metrics/{metric_id}"),
        ("GET", "/v1/persons/{person_id}/health-score"),
        ("GET", "/v1/persons/{person_id}/risk-alerts"),
        ("POST", "/v1/persons/{person_id}/symptoms"),
        ("GET", "/v1/persons/{person_id}/symptoms"),
        ("GET", "/v1/persons/{person_id}/symptoms/{symptom_id}"),
        ("POST", "/v1/persons/{person_id}/actions"),
        ("GET", "/v1/persons/{person_id}/actions"),
        ("GET", "/v1/persons/{person_id}/actions/{action_id}"),
        ("POST", "/v1/persons/{person_id}/actions/{action_id}/complete"),
        ("POST", "/v1/persons/{person_id}/actions/{action_id}/outcomes"),
        ("GET", "/v1/persons/{person_id}/actions/{action_id}/outcomes"),
        (
            "GET",
            "/v1/persons/{person_id}/actions/{action_id}/outcomes/{outcome_id}",
        ),
        ("POST", "/v1/persons/{person_id}/reports"),
        ("GET", "/v1/persons/{person_id}/reports"),
        ("GET", "/v1/persons/{person_id}/reports/{report_id}"),
        ("POST", "/v1/persons/{person_id}/reports/{report_id}/confirm"),
        ("GET", "/v1/persons/{person_id}/assistant/today"),
        ("GET", "/v1/persons/{person_id}/history"),
        ("GET", "/v1/persons/{person_id}/analytics"),
    }

    security_schemes = document["components"]["securitySchemes"]
    assert security_schemes == {
        "CookieSession": {
            "type": "apiKey",
            "description": "Opaque server-managed session cookie.",
            "in": "cookie",
            "name": "healthy_session",
        }
    }
    assert "bearer" not in str(document).casefold()


def test_production_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEALTHY_ENV", "production")
    monkeypatch.setenv("HEALTHY_DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("HEALTHY_ALLOWED_ORIGINS", "https://healthy.example")
    monkeypatch.setenv("HEALTHY_CSRF_SECRET", os.urandom(32).hex())
    monkeypatch.setenv("HEALTHY_COOKIE_SECURE", "false")
    with pytest.raises(RuntimeError, match="secure session cookies"):
        Settings.from_env()


def test_migration_created_required_postgres_constraints_and_indexes() -> None:
    inspector = inspect(Database(DATABASE_URL).engine)
    assert set(inspector.get_table_names()) >= {
        "alembic_version",
        "accounts",
        "sessions",
        "persons",
        "health_metrics",
        "symptom_logs",
        "health_actions",
        "health_action_outcomes",
    }
    assert {index["name"] for index in inspector.get_indexes("persons")} >= {
        "ix_persons_owner_account_id",
        "uq_persons_one_default_per_account",
    }
    assert {
        foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("persons")
    } == {"accounts"}
    assert {constraint["name"] for constraint in inspector.get_check_constraints("persons")} >= {
        "ck_persons_default_relationship_self"
    }
    assert {index["name"] for index in inspector.get_indexes("health_metrics")} >= {
        "ix_health_metrics_person_id"
    }
    sleep_column = next(
        column
        for column in inspector.get_columns("health_metrics")
        if column["name"] == "sleep_hours"
    )
    assert sleep_column["nullable"] is True
    assert sleep_column["type"].precision == 4
    assert sleep_column["type"].scale == 2
    health_metric_foreign_keys = inspector.get_foreign_keys("health_metrics")
    assert {foreign_key["referred_table"] for foreign_key in health_metric_foreign_keys} == {
        "persons"
    }
    assert {
        foreign_key["options"].get("ondelete") for foreign_key in health_metric_foreign_keys
    } == {"CASCADE"}
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("health_metrics")
    } == {
        "ck_health_metrics_bp_pairing",
        "ck_health_metrics_at_least_one_value",
        "ck_health_metrics_systolic_bp_mm_hg_bounds",
        "ck_health_metrics_diastolic_bp_mm_hg_bounds",
        "ck_health_metrics_heart_rate_bpm_bounds",
        "ck_health_metrics_steps_bounds",
        "ck_health_metrics_weight_kg_bounds",
        "ck_health_metrics_blood_glucose_mg_dl_bounds",
    }
    assert {index["name"] for index in inspector.get_indexes("symptom_logs")} >= {
        "ix_symptom_logs_person_id",
        "ix_symptom_logs_person_timeline",
    }
    symptom_foreign_keys = inspector.get_foreign_keys("symptom_logs")
    assert {foreign_key["referred_table"] for foreign_key in symptom_foreign_keys} == {"persons"}
    assert {foreign_key["options"].get("ondelete") for foreign_key in symptom_foreign_keys} == {
        "CASCADE"
    }
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("symptom_logs")
    } == {
        "ck_symptom_logs_symptom_length",
        "ck_symptom_logs_symptom_trimmed",
        "ck_symptom_logs_severity_bounds",
        "ck_symptom_logs_duration_minutes_minimum",
        "ck_symptom_logs_estimated_duration_days_bounds",
        "ck_symptom_logs_note_length",
    }
    assert {index["name"] for index in inspector.get_indexes("health_actions")} >= {
        "ix_health_actions_person_id",
        "ix_health_actions_person_timeline",
    }
    health_action_foreign_keys = inspector.get_foreign_keys("health_actions")
    assert {foreign_key["referred_table"] for foreign_key in health_action_foreign_keys} == {
        "persons"
    }
    assert {
        foreign_key["options"].get("ondelete") for foreign_key in health_action_foreign_keys
    } == {"CASCADE"}
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("health_actions")
    } == {
        "ck_health_actions_title_length",
        "ck_health_actions_title_trimmed",
        "ck_health_actions_status_allowed",
        "ck_health_actions_status_completion_consistent",
        "ck_health_actions_description_length",
    }
    assert {column["name"] for column in inspector.get_columns("health_action_outcomes")} == {
        "id",
        "action_id",
        "note",
        "observed_at",
        "created_at",
    }
    assert {index["name"] for index in inspector.get_indexes("health_action_outcomes")} >= {
        "ix_health_action_outcomes_action_id",
        "ix_health_action_outcomes_action_timeline",
    }
    health_action_outcome_foreign_keys = inspector.get_foreign_keys("health_action_outcomes")
    assert {
        foreign_key["referred_table"] for foreign_key in health_action_outcome_foreign_keys
    } == {"health_actions"}
    assert {
        foreign_key["options"].get("ondelete") for foreign_key in health_action_outcome_foreign_keys
    } == {"CASCADE"}
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("health_action_outcomes")
    } == {
        "ck_health_action_outcomes_note_length",
        "ck_health_action_outcomes_note_trimmed",
    }


def test_runtime_never_auto_creates_schema() -> None:
    api_root = Path(__file__).resolve().parents[2] / "apps" / "api"
    source = "\n".join(path.read_text(encoding="utf-8") for path in api_root.rglob("*.py"))
    forbidden = "create" + "_all"
    assert forbidden not in source
