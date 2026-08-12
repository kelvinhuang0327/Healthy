from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from healthy.domain.actions import HealthActionStatus

MAX_TIMEZONE_NAME_LENGTH = 128


class InvalidTimezoneError(ValueError):
    pass


class InvalidLocalTimeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReminderDueState:
    is_due: bool
    local_date: date


def timezone_for(timezone_name: str) -> ZoneInfo:
    normalized = timezone_name.strip()
    if not normalized or len(normalized) > MAX_TIMEZONE_NAME_LENGTH:
        raise InvalidTimezoneError("timezone_name must be a valid IANA timezone")
    try:
        return ZoneInfo(normalized)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise InvalidTimezoneError("timezone_name must be a valid IANA timezone") from error


def validate_timezone(timezone_name: str) -> str:
    normalized = timezone_name.strip()
    timezone_for(normalized)
    return normalized


def normalize_local_time(local_time: time) -> time:
    if local_time.tzinfo is not None:
        raise InvalidLocalTimeError("local_time must not include a timezone offset")
    return local_time.replace(microsecond=0)


def local_datetime(now: datetime, timezone_name: str) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must include timezone information")
    return now.astimezone(UTC).astimezone(timezone_for(timezone_name))


def local_date_for(now: datetime, timezone_name: str) -> date:
    return local_datetime(now, timezone_name).date()


def normalize_instant(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("instant must include timezone information")
    return value.astimezone(UTC)


def evaluate_due(
    *,
    action_status: str,
    timezone_name: str,
    local_time: time,
    now: datetime,
    snoozed_until: datetime | None,
    last_acknowledged_local_date: date | None,
) -> ReminderDueState:
    current_utc = normalize_instant(now)
    current_local = local_datetime(current_utc, timezone_name)
    normalized_local_time = normalize_local_time(local_time)
    snooze_active = snoozed_until is not None and normalize_instant(snoozed_until) > current_utc
    acknowledged = last_acknowledged_local_date == current_local.date()
    due = (
        action_status == HealthActionStatus.TODO
        and current_local.time().replace(tzinfo=None) >= normalized_local_time
        and not snooze_active
        and not acknowledged
    )
    return ReminderDueState(is_due=due, local_date=current_local.date())
