from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field


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


def _optional_text(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    normalized = raw.strip()
    return normalized or None


def _port(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if not 1 <= value <= 65_535:
        raise RuntimeError(f"{name} must be between 1 and 65535")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    database_url: str
    cookie_secure: bool
    allowed_origins: frozenset[str]
    csrf_secret: bytes
    session_max_age_seconds: int = 28_800
    email_notifications_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_from_address: str | None = None
    smtp_starttls: bool = True
    smtp_username: str | None = field(default=None, repr=False)
    smtp_password: str | None = field(default=None, repr=False)

    @property
    def email_delivery_available(self) -> bool:
        return (
            self.email_notifications_enabled
            and bool(self.smtp_host)
            and bool(self.smtp_from_address)
            and self.smtp_starttls
            and ((self.smtp_username is None) == (self.smtp_password is None))
        )

    @classmethod
    def from_env(cls) -> Settings:
        environment = os.getenv("HEALTHY_ENV", "development").strip().casefold()
        explicit_database_url = os.getenv("HEALTHY_DATABASE_URL")
        explicit_origins = os.getenv("HEALTHY_ALLOWED_ORIGINS")
        explicit_csrf_secret = os.getenv("HEALTHY_CSRF_SECRET")
        cookie_secure = _boolean("HEALTHY_COOKIE_SECURE", True)
        email_notifications_enabled = _boolean("HEALTHY_EMAIL_NOTIFICATIONS_ENABLED", False)
        smtp_host = _optional_text("HEALTHY_SMTP_HOST")
        smtp_port = _port("HEALTHY_SMTP_PORT", 587)
        smtp_from_address = _optional_text("HEALTHY_SMTP_FROM_ADDRESS")
        smtp_starttls = _boolean("HEALTHY_SMTP_STARTTLS", True)
        smtp_username = _optional_text("HEALTHY_SMTP_USERNAME")
        smtp_password = _optional_text("HEALTHY_SMTP_PASSWORD")

        if (smtp_username is None) != (smtp_password is None):
            raise RuntimeError("SMTP username and password must be configured together")
        if email_notifications_enabled and (smtp_host is None or smtp_from_address is None):
            raise RuntimeError("Enabled email notifications require SMTP host and sender address")

        if environment == "production":
            if not explicit_database_url:
                raise RuntimeError("Production requires HEALTHY_DATABASE_URL")
            if not cookie_secure:
                raise RuntimeError("Production requires secure session cookies")
            if not explicit_origins:
                raise RuntimeError("Production requires HEALTHY_ALLOWED_ORIGINS")
            if not explicit_csrf_secret or len(explicit_csrf_secret.encode()) < 32:
                raise RuntimeError("Production requires a strong HEALTHY_CSRF_SECRET")
            if email_notifications_enabled and not smtp_starttls:
                raise RuntimeError("Production email notifications require secure SMTP transport")

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
            email_notifications_enabled=email_notifications_enabled,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_from_address=smtp_from_address,
            smtp_starttls=smtp_starttls,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
        )
