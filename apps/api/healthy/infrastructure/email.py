from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Protocol

from healthy.domain.notifications import GENERIC_EMAIL_BODY, GENERIC_EMAIL_SUBJECT
from healthy.infrastructure.config import Settings


class EmailTransport(Protocol):
    def send(self, *, recipient: str, subject: str, body: str) -> None:
        """Send one already-rendered email message."""


class SMTPEmailTransport:
    def __init__(self, settings: Settings, *, timeout_seconds: float = 10.0) -> None:
        self._settings = settings
        self._timeout_seconds = timeout_seconds

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        if not self._settings.email_delivery_available:
            raise RuntimeError("Email delivery capability is unavailable")
        smtp_host = self._settings.smtp_host
        if smtp_host is None:
            raise RuntimeError("Email delivery capability is unavailable")
        message = EmailMessage()
        message["From"] = self._settings.smtp_from_address or ""
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(
            smtp_host,
            self._settings.smtp_port,
            timeout=self._timeout_seconds,
        ) as server:
            if self._settings.smtp_starttls:
                server.starttls()
            if self._settings.smtp_username is not None:
                server.login(
                    self._settings.smtp_username,
                    self._settings.smtp_password or "",
                )
            server.send_message(message)


def generic_email_payload() -> tuple[str, str]:
    return GENERIC_EMAIL_SUBJECT, GENERIC_EMAIL_BODY
