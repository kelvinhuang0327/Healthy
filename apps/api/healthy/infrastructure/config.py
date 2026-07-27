from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    database_url: str
    cookie_secure: bool
    allowed_origins: frozenset[str]
    csrf_secret: bytes
    session_max_age_seconds: int = 28_800

    @classmethod
    def from_env(cls) -> Settings:
        environment = os.getenv("HEALTHY_ENV", "development").strip().casefold()
        explicit_database_url = os.getenv("HEALTHY_DATABASE_URL")
        explicit_origins = os.getenv("HEALTHY_ALLOWED_ORIGINS")
        explicit_csrf_secret = os.getenv("HEALTHY_CSRF_SECRET")
        cookie_secure = _boolean("HEALTHY_COOKIE_SECURE", True)

        if environment == "production":
            if not explicit_database_url:
                raise RuntimeError("Production requires HEALTHY_DATABASE_URL")
            if not cookie_secure:
                raise RuntimeError("Production requires secure session cookies")
            if not explicit_origins:
                raise RuntimeError("Production requires HEALTHY_ALLOWED_ORIGINS")
            if not explicit_csrf_secret or len(explicit_csrf_secret.encode()) < 32:
                raise RuntimeError("Production requires a strong HEALTHY_CSRF_SECRET")

        database_url = (
            explicit_database_url or "postgresql+psycopg://healthy@127.0.0.1:55432/healthy_test"
        )
        origins = frozenset(
            origin.strip().rstrip("/")
            for origin in (explicit_origins or "http://127.0.0.1:3000,http://localhost:3000").split(
                ","
            )
            if origin.strip()
        )
        csrf_secret = (
            explicit_csrf_secret.encode() if explicit_csrf_secret else secrets.token_bytes(32)
        )
        return cls(
            environment=environment,
            database_url=database_url,
            cookie_secure=cookie_secure,
            allowed_origins=origins,
            csrf_secret=csrf_secret,
        )
