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
    }
    operations = {
        (method.upper(), path)
        for path, methods in document["paths"].items()
        for method in methods
        if method in {"get", "post", "delete"}
    }
    assert operations == {
        ("POST", "/v1/accounts"),
        ("POST", "/v1/sessions"),
        ("DELETE", "/v1/sessions/current"),
        ("GET", "/v1/session"),
        ("GET", "/v1/persons"),
        ("POST", "/v1/persons"),
        ("GET", "/v1/persons/{person_id}"),
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


def test_runtime_never_auto_creates_schema() -> None:
    api_root = Path(__file__).resolve().parents[2] / "apps" / "api"
    source = "\n".join(path.read_text(encoding="utf-8") for path in api_root.rglob("*.py"))
    forbidden = "create" + "_all"
    assert forbidden not in source
