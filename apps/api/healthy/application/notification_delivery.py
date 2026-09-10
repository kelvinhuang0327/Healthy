from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy.orm import Session

from healthy.domain import notifications as notifications_domain
from healthy.domain import reminders as reminders_domain
from healthy.infrastructure.config import Settings
from healthy.infrastructure.email import EmailTransport
from healthy.infrastructure.models import NotificationDelivery
from healthy.infrastructure.repositories import NotificationDeliveryRepository

STALE_CLAIM_AFTER = timedelta(minutes=15)
DEFAULT_MAX_DELIVERIES_PER_TICK = 100


@dataclass(frozen=True, slots=True)
class NotificationDeliveryTickResult:
    capability_available: bool
    enqueued: int = 0
    stale_reconciled: int = 0
    claimed: int = 0
    sent: int = 0
    cancelled: int = 0
    failed: int = 0
    skipped_send: int = 0


def _utc_now(now: datetime | None) -> datetime:
    return reminders_domain.normalize_instant(now or datetime.now(UTC))


def enqueue_due_email_deliveries(
    database_session: Session,
    *,
    now: datetime | None = None,
) -> list[NotificationDelivery]:
    """Create one pending email intent per eligible reminder and local date."""

    current = _utc_now(now)
    created: list[NotificationDelivery] = []
    for _account, reminder, action in NotificationDeliveryRepository.list_due_email_candidates(
        database_session
    ):
        due_state = reminders_domain.evaluate_due(
            action_status=action.status,
            timezone_name=reminder.timezone_name,
            local_time=reminder.local_time,
            now=current,
            snoozed_until=reminder.snoozed_until,
            last_acknowledged_local_date=reminder.last_acknowledged_local_date,
        )
        if not due_state.is_due:
            continue
        delivery = NotificationDeliveryRepository.create_pending_if_absent(
            database_session,
            reminder_id=reminder.id,
            reminder_local_date=due_state.local_date,
            created_at=current,
        )
        if delivery is not None:
            created.append(delivery)
    database_session.commit()
    return created


def reconcile_stale_sending_deliveries(
    database_session: Session,
    *,
    now: datetime | None = None,
    stale_after: timedelta = STALE_CLAIM_AFTER,
) -> int:
    current = _utc_now(now)
    stale_deliveries = NotificationDeliveryRepository.list_stale_sending(
        database_session,
        before=current - stale_after,
    )
    for delivery in stale_deliveries:
        NotificationDeliveryRepository.mark_unknown(
            database_session,
            delivery,
            updated_at=current,
            failure_code=notifications_domain.NotificationFailureCode.STALE_CLAIM,
        )
    if stale_deliveries:
        database_session.commit()
    return len(stale_deliveries)


def _delivery_is_currently_eligible(
    delivery: NotificationDelivery,
    *,
    account_status: str,
    reminder_email_enabled: bool,
    action_status: str,
    timezone_name: str,
    local_time: time,
    snoozed_until: datetime | None,
    last_acknowledged_local_date: date | None,
    now: datetime,
) -> bool:
    if account_status != "active" or not reminder_email_enabled:
        return False
    due_state = reminders_domain.evaluate_due(
        action_status=action_status,
        timezone_name=timezone_name,
        local_time=local_time,
        now=now,
        snoozed_until=snoozed_until,
        last_acknowledged_local_date=last_acknowledged_local_date,
    )
    return due_state.is_due and due_state.local_date == delivery.reminder_local_date


def _cancel_delivery(
    database_session: Session,
    delivery: NotificationDelivery,
    *,
    now: datetime,
) -> None:
    NotificationDeliveryRepository.mark_cancelled(
        database_session,
        delivery,
        updated_at=now,
    )
    database_session.commit()


def dispatch_pending_email_deliveries(
    database_session: Session,
    *,
    transport: EmailTransport,
    now: datetime | None = None,
    max_deliveries: int = DEFAULT_MAX_DELIVERIES_PER_TICK,
) -> NotificationDeliveryTickResult:
    """Claim and attempt each pending delivery at most once for this tick."""

    current = _utc_now(now)
    claimed_count = 0
    sent_count = 0
    cancelled_count = 0
    failed_count = 0
    while claimed_count < max_deliveries:
        delivery = NotificationDeliveryRepository.claim_next_pending(
            database_session,
            claimed_at=current,
        )
        if delivery is None:
            break
        claimed_count += 1
        delivery_id = delivery.id
        # A claim is durable before any transport invocation. A disappeared
        # worker is therefore reconciled as unknown rather than retried.
        database_session.commit()

        context = NotificationDeliveryRepository.get_context(database_session, delivery_id)
        if context is None:
            delivery = NotificationDeliveryRepository.get_by_id(database_session, delivery_id)
            if delivery is not None:
                _cancel_delivery(database_session, delivery, now=current)
            cancelled_count += 1
            continue

        delivery, account, reminder, action = context
        if not _delivery_is_currently_eligible(
            delivery,
            account_status=account.status,
            reminder_email_enabled=reminder.email_enabled,
            action_status=action.status,
            timezone_name=reminder.timezone_name,
            local_time=reminder.local_time,
            snoozed_until=reminder.snoozed_until,
            last_acknowledged_local_date=reminder.last_acknowledged_local_date,
            now=current,
        ):
            _cancel_delivery(database_session, delivery, now=current)
            cancelled_count += 1
            continue

        try:
            transport.send(
                recipient=account.normalized_email,
                subject=notifications_domain.GENERIC_EMAIL_SUBJECT,
                body=notifications_domain.GENERIC_EMAIL_BODY,
            )
        except Exception:
            NotificationDeliveryRepository.mark_failed(
                database_session,
                delivery,
                failed_at=current,
                failure_code=notifications_domain.NotificationFailureCode.TRANSPORT_ERROR,
            )
            database_session.commit()
            failed_count += 1
        else:
            NotificationDeliveryRepository.mark_sent(
                database_session,
                delivery,
                sent_at=current,
            )
            database_session.commit()
            sent_count += 1

    return NotificationDeliveryTickResult(
        capability_available=True,
        claimed=claimed_count,
        sent=sent_count,
        cancelled=cancelled_count,
        failed=failed_count,
    )


def process_notification_delivery_tick(
    database_session: Session,
    *,
    settings: Settings,
    send: bool = False,
    transport: EmailTransport | None = None,
    now: datetime | None = None,
    max_deliveries: int = DEFAULT_MAX_DELIVERIES_PER_TICK,
) -> NotificationDeliveryTickResult:
    """Run one bounded enqueue/reconcile/dispatch worker tick."""

    if not settings.email_delivery_available:
        return NotificationDeliveryTickResult(capability_available=False)

    current = _utc_now(now)
    enqueued = enqueue_due_email_deliveries(database_session, now=current)
    stale_reconciled = reconcile_stale_sending_deliveries(database_session, now=current)
    if not send:
        return NotificationDeliveryTickResult(
            capability_available=True,
            enqueued=len(enqueued),
            stale_reconciled=stale_reconciled,
            skipped_send=len(enqueued),
        )
    if transport is None:
        raise ValueError("A transport is required when send is enabled")

    dispatched = dispatch_pending_email_deliveries(
        database_session,
        transport=transport,
        now=current,
        max_deliveries=max_deliveries,
    )
    return NotificationDeliveryTickResult(
        capability_available=True,
        enqueued=len(enqueued),
        stale_reconciled=stale_reconciled,
        claimed=dispatched.claimed,
        sent=dispatched.sent,
        cancelled=dispatched.cancelled,
        failed=dispatched.failed,
    )


# Explicit aliases keep the command vocabulary discoverable to future callers.
enqueue_due_notification_deliveries = enqueue_due_email_deliveries
process_notification_deliveries = process_notification_delivery_tick
