from __future__ import annotations

from enum import StrEnum


class NotificationChannel(StrEnum):
    EMAIL = "email"


class NotificationDeliveryStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN = "unknown"


class NotificationFailureCode(StrEnum):
    TRANSPORT_ERROR = "transport_error"
    CONFIGURATION_ERROR = "configuration_error"
    STALE_CLAIM = "stale_claim"


GENERIC_EMAIL_SUBJECT = "Healthy reminder"
GENERIC_EMAIL_BODY = "You have a reminder waiting in Healthy. Open Healthy to review it."
