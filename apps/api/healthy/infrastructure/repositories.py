from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session, joinedload

from healthy.domain.actions import HealthActionOriginType, HealthActionStatus
from healthy.domain.external_imports import (
    SOURCE_TYPE_EXTERNAL_CSV,
    SOURCE_TYPE_MANUAL,
    ParsedHealthMetricRow,
)
from healthy.domain.identity import AccountStatus
from healthy.domain.notifications import NotificationChannel, NotificationDeliveryStatus
from healthy.infrastructure.models import (
    Account,
    HealthAction,
    HealthActionOutcome,
    HealthActionReminder,
    HealthMetric,
    HealthReportModel,
    HealthReportObservationModel,
    NotificationDelivery,
    Person,
    SymptomLog,
)


class PersonRepository:
    @staticmethod
    def list_for_owner(database_session: Session, owner_account_id: uuid.UUID) -> list[Person]:
        statement = (
            select(Person)
            .where(Person.owner_account_id == owner_account_id)
            .order_by(Person.is_default.desc(), Person.created_at.asc())
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def get_for_owner(
        database_session: Session,
        owner_account_id: uuid.UUID,
        person_id: uuid.UUID,
    ) -> Person | None:
        statement = select(Person).where(
            Person.id == person_id,
            Person.owner_account_id == owner_account_id,
        )
        return database_session.scalar(statement)

    @staticmethod
    def create_non_default(
        database_session: Session,
        owner_account_id: uuid.UUID,
        display_name: str,
        relationship: str,
    ) -> Person:
        person = Person(
            owner_account_id=owner_account_id,
            display_name=display_name,
            relationship=relationship,
            is_default=False,
        )
        database_session.add(person)
        return person

    @staticmethod
    def update_height_for_owner(
        database_session: Session,
        owner_account_id: uuid.UUID,
        person_id: uuid.UUID,
        height_cm: Decimal | None,
    ) -> Person | None:
        statement = select(Person).where(
            Person.id == person_id,
            Person.owner_account_id == owner_account_id,
        )
        person = database_session.scalar(statement)
        if person is None:
            return None
        person.height_cm = height_cm
        return person


class HealthMetricRepository:
    @staticmethod
    def create_for_person(
        database_session: Session,
        person_id: uuid.UUID,
        *,
        recorded_at: datetime,
        systolic_bp_mm_hg: int | None,
        diastolic_bp_mm_hg: int | None,
        heart_rate_bpm: int | None,
        steps: int | None,
        weight_kg: Decimal | None,
        blood_glucose_mg_dl: Decimal | None,
        sleep_hours: Decimal | None,
        note: str | None,
    ) -> HealthMetric:
        metric = HealthMetric(
            person_id=person_id,
            recorded_at=recorded_at,
            systolic_bp_mm_hg=systolic_bp_mm_hg,
            diastolic_bp_mm_hg=diastolic_bp_mm_hg,
            heart_rate_bpm=heart_rate_bpm,
            steps=steps,
            weight_kg=weight_kg,
            blood_glucose_mg_dl=blood_glucose_mg_dl,
            sleep_hours=sleep_hours,
            note=note,
            source_type=SOURCE_TYPE_MANUAL,
            source_record_fingerprint=None,
        )
        database_session.add(metric)
        return metric

    @staticmethod
    def import_external_metrics(
        database_session: Session,
        person_id: uuid.UUID,
        rows: list[ParsedHealthMetricRow],
    ) -> int:
        if not rows:
            return 0
        seen_fingerprints: set[str] = set()
        unique_rows: list[ParsedHealthMetricRow] = []
        for row in rows:
            if row.source_record_fingerprint not in seen_fingerprints:
                seen_fingerprints.add(row.source_record_fingerprint)
                unique_rows.append(row)

        inserted_count = 0
        for row in unique_rows:
            statement = (
                postgresql_insert(HealthMetric)
                .values(
                    person_id=person_id,
                    recorded_at=row.recorded_at,
                    systolic_bp_mm_hg=row.systolic_bp_mm_hg,
                    diastolic_bp_mm_hg=row.diastolic_bp_mm_hg,
                    heart_rate_bpm=row.heart_rate_bpm,
                    steps=row.steps,
                    weight_kg=row.weight_kg,
                    blood_glucose_mg_dl=row.blood_glucose_mg_dl,
                    sleep_hours=row.sleep_hours,
                    note=row.note,
                    source_type=SOURCE_TYPE_EXTERNAL_CSV,
                    source_record_fingerprint=row.source_record_fingerprint,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        HealthMetric.person_id,
                        HealthMetric.source_type,
                        HealthMetric.source_record_fingerprint,
                    ],
                    index_where=text("source_record_fingerprint IS NOT NULL"),
                )
                .returning(HealthMetric.id)
            )
            result = database_session.execute(statement).scalar_one_or_none()
            if result is not None:
                inserted_count += 1

        database_session.flush()
        return inserted_count

    @staticmethod
    def list_for_person(database_session: Session, person_id: uuid.UUID) -> list[HealthMetric]:
        statement = (
            select(HealthMetric)
            .where(HealthMetric.person_id == person_id)
            .order_by(
                HealthMetric.recorded_at.desc(),
                HealthMetric.created_at.desc(),
                HealthMetric.id.desc(),
            )
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def get_for_person(
        database_session: Session,
        person_id: uuid.UUID,
        metric_id: uuid.UUID,
    ) -> HealthMetric | None:
        statement = select(HealthMetric).where(
            HealthMetric.id == metric_id,
            HealthMetric.person_id == person_id,
        )
        return database_session.scalar(statement)

    @staticmethod
    def get_latest_for_person(
        database_session: Session,
        person_id: uuid.UUID,
    ) -> HealthMetric | None:
        statement = (
            select(HealthMetric)
            .where(HealthMetric.person_id == person_id)
            .order_by(
                HealthMetric.recorded_at.desc(),
                HealthMetric.created_at.desc(),
                HealthMetric.id.desc(),
            )
            .limit(1)
        )
        return database_session.scalar(statement)

    @staticmethod
    def count_for_person(database_session: Session, person_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(HealthMetric)
            .where(HealthMetric.person_id == person_id)
        )
        return database_session.scalar(statement) or 0


class SymptomLogRepository:
    @staticmethod
    def create_for_person(
        database_session: Session,
        person_id: uuid.UUID,
        *,
        symptom: str,
        occurred_at: datetime,
        severity: int,
        duration_minutes: int | None,
        estimated_start_date: date | None,
        estimated_duration_days: int | None,
        note: str | None,
    ) -> SymptomLog:
        symptom_log = SymptomLog(
            person_id=person_id,
            symptom=symptom,
            occurred_at=occurred_at,
            severity=severity,
            duration_minutes=duration_minutes,
            estimated_start_date=estimated_start_date,
            estimated_duration_days=estimated_duration_days,
            note=note,
        )
        database_session.add(symptom_log)
        return symptom_log

    @staticmethod
    def list_for_person(database_session: Session, person_id: uuid.UUID) -> list[SymptomLog]:
        statement = (
            select(SymptomLog)
            .where(SymptomLog.person_id == person_id)
            .order_by(
                SymptomLog.occurred_at.desc(),
                SymptomLog.created_at.desc(),
                SymptomLog.id.desc(),
            )
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def get_for_person(
        database_session: Session,
        person_id: uuid.UUID,
        symptom_id: uuid.UUID,
    ) -> SymptomLog | None:
        statement = select(SymptomLog).where(
            SymptomLog.id == symptom_id,
            SymptomLog.person_id == person_id,
        )
        return database_session.scalar(statement)

    @staticmethod
    def list_since_for_person(
        database_session: Session,
        person_id: uuid.UUID,
        since: datetime,
    ) -> list[SymptomLog]:
        statement = (
            select(SymptomLog)
            .where(
                SymptomLog.person_id == person_id,
                SymptomLog.occurred_at >= since,
            )
            .order_by(
                SymptomLog.occurred_at.desc(),
                SymptomLog.created_at.desc(),
                SymptomLog.id.desc(),
            )
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def count_for_person(database_session: Session, person_id: uuid.UUID) -> int:
        statement = (
            select(func.count()).select_from(SymptomLog).where(SymptomLog.person_id == person_id)
        )
        return database_session.scalar(statement) or 0


class HealthActionRepository:
    @staticmethod
    def create_for_person(
        database_session: Session,
        person_id: uuid.UUID,
        *,
        title: str,
        description: str | None,
        due_at: datetime | None,
    ) -> HealthAction:
        action = HealthAction(
            person_id=person_id,
            title=title,
            description=description,
            due_at=due_at,
            origin_type=HealthActionOriginType.MANUAL,
        )
        database_session.add(action)
        return action

    @staticmethod
    def create_from_recommendation(
        database_session: Session,
        person_id: uuid.UUID,
        *,
        title: str,
        description: str | None,
        recommendation_fingerprint: str,
        recommendation_code: str,
        recommendation_rule_version: str,
        source_rule_code: str,
        source_evidence_kind: str,
        source_evidence_id: uuid.UUID,
        source_observation_id: uuid.UUID | None,
        source_report_id: uuid.UUID | None,
        source_evidence_observed_at: datetime,
    ) -> HealthAction:
        action = HealthAction(
            person_id=person_id,
            title=title,
            description=description,
            due_at=None,
            origin_type=HealthActionOriginType.ACTION_RECOMMENDATION,
            recommendation_fingerprint=recommendation_fingerprint,
            recommendation_code=recommendation_code,
            recommendation_rule_version=recommendation_rule_version,
            source_rule_code=source_rule_code,
            source_evidence_kind=source_evidence_kind,
            source_evidence_id=source_evidence_id,
            source_observation_id=source_observation_id,
            source_report_id=source_report_id,
            source_evidence_observed_at=source_evidence_observed_at,
        )
        database_session.add(action)
        return action

    @staticmethod
    def get_by_recommendation_fingerprint(
        database_session: Session,
        person_id: uuid.UUID,
        recommendation_fingerprint: str,
    ) -> HealthAction | None:
        statement = select(HealthAction).where(
            HealthAction.person_id == person_id,
            HealthAction.recommendation_fingerprint == recommendation_fingerprint,
        )
        return database_session.scalar(statement)

    @staticmethod
    def list_for_person(database_session: Session, person_id: uuid.UUID) -> list[HealthAction]:
        statement = (
            select(HealthAction)
            .where(HealthAction.person_id == person_id)
            .order_by(
                HealthAction.created_at.desc(),
                HealthAction.id.desc(),
            )
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def get_for_person(
        database_session: Session,
        person_id: uuid.UUID,
        action_id: uuid.UUID,
    ) -> HealthAction | None:
        statement = select(HealthAction).where(
            HealthAction.id == action_id,
            HealthAction.person_id == person_id,
        )
        return database_session.scalar(statement)

    @classmethod
    def complete_for_person(
        cls,
        database_session: Session,
        person_id: uuid.UUID,
        action_id: uuid.UUID,
        completion_instant: datetime,
    ) -> HealthAction | None:
        statement = (
            update(HealthAction)
            .where(
                HealthAction.id == action_id,
                HealthAction.person_id == person_id,
                HealthAction.status == HealthActionStatus.TODO,
            )
            .values(
                status=HealthActionStatus.DONE,
                completed_at=completion_instant,
                updated_at=completion_instant,
            )
            .returning(HealthAction)
            .execution_options(synchronize_session=False)
        )
        transitioned = database_session.scalars(statement).one_or_none()
        if transitioned is not None:
            return transitioned
        return cls.get_for_person(database_session, person_id, action_id)

    @staticmethod
    def list_open_or_recently_completed_for_person(
        database_session: Session,
        person_id: uuid.UUID,
        since: datetime,
    ) -> list[HealthAction]:
        statement = (
            select(HealthAction)
            .where(
                HealthAction.person_id == person_id,
                (HealthAction.status == HealthActionStatus.TODO)
                | (
                    (HealthAction.status == HealthActionStatus.DONE)
                    & (HealthAction.completed_at.is_not(None))
                    & (HealthAction.completed_at >= since)
                ),
            )
            .order_by(
                HealthAction.created_at.desc(),
                HealthAction.id.desc(),
            )
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def count_for_person(database_session: Session, person_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(HealthAction)
            .where(HealthAction.person_id == person_id)
        )
        return database_session.scalar(statement) or 0


class HealthActionReminderRepository:
    @staticmethod
    def get_for_action(
        database_session: Session,
        action_id: uuid.UUID,
    ) -> HealthActionReminder | None:
        statement = select(HealthActionReminder).where(
            HealthActionReminder.action_id == action_id,
        )
        with database_session.no_autoflush:
            return database_session.scalar(statement)

    @staticmethod
    def upsert_for_action(
        database_session: Session,
        action_id: uuid.UUID,
        *,
        timezone_name: str,
        local_time: time,
        updated_at: datetime,
    ) -> HealthActionReminder:
        statement = (
            postgresql_insert(HealthActionReminder)
            .values(
                action_id=action_id,
                timezone_name=timezone_name,
                local_time=local_time,
            )
            .on_conflict_do_update(
                index_elements=[HealthActionReminder.action_id],
                set_={
                    "timezone_name": timezone_name,
                    "local_time": local_time,
                    "updated_at": updated_at,
                },
            )
            .returning(HealthActionReminder)
            .execution_options(populate_existing=True)
        )
        return database_session.scalars(statement).one()

    @staticmethod
    def delete_for_action(database_session: Session, action_id: uuid.UUID) -> bool:
        statement = (
            delete(HealthActionReminder)
            .where(HealthActionReminder.action_id == action_id)
            .returning(HealthActionReminder.id)
        )
        return database_session.scalar(statement) is not None

    @staticmethod
    def list_for_person(
        database_session: Session,
        person_id: uuid.UUID,
    ) -> list[tuple[HealthActionReminder, HealthAction]]:
        statement = (
            select(HealthActionReminder, HealthAction)
            .join(HealthAction, HealthAction.id == HealthActionReminder.action_id)
            .where(HealthAction.person_id == person_id)
            .order_by(HealthAction.created_at.desc(), HealthAction.id.desc())
        )
        with database_session.no_autoflush:
            rows = database_session.execute(statement).all()
        return [(row[0], row[1]) for row in rows]

    @staticmethod
    def acknowledge_for_action(
        database_session: Session,
        action_id: uuid.UUID,
        *,
        local_date: date,
        updated_at: datetime,
    ) -> HealthActionReminder | None:
        statement = (
            update(HealthActionReminder)
            .where(HealthActionReminder.action_id == action_id)
            .values(
                last_acknowledged_local_date=local_date,
                updated_at=updated_at,
            )
            .returning(HealthActionReminder)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        return database_session.scalars(statement).one_or_none()

    @staticmethod
    def set_snoozed_until(
        database_session: Session,
        action_id: uuid.UUID,
        *,
        snoozed_until: datetime,
        updated_at: datetime,
    ) -> HealthActionReminder | None:
        statement = (
            update(HealthActionReminder)
            .where(HealthActionReminder.action_id == action_id)
            .values(
                snoozed_until=snoozed_until,
                updated_at=updated_at,
            )
            .returning(HealthActionReminder)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        return database_session.scalars(statement).one_or_none()

    @staticmethod
    def set_email_enabled(
        database_session: Session,
        action_id: uuid.UUID,
        *,
        email_enabled: bool,
        updated_at: datetime,
    ) -> HealthActionReminder | None:
        statement = (
            update(HealthActionReminder)
            .where(HealthActionReminder.action_id == action_id)
            .values(
                email_enabled=email_enabled,
                updated_at=updated_at,
            )
            .returning(HealthActionReminder)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        return database_session.scalars(statement).one_or_none()


class NotificationDeliveryRepository:
    @staticmethod
    def list_due_email_candidates(
        database_session: Session,
    ) -> list[tuple[Account, HealthActionReminder, HealthAction]]:
        statement = (
            select(Account, HealthActionReminder, HealthAction)
            .join(Person, Person.owner_account_id == Account.id)
            .join(HealthAction, HealthAction.person_id == Person.id)
            .join(HealthActionReminder, HealthActionReminder.action_id == HealthAction.id)
            .where(
                Account.status == AccountStatus.ACTIVE,
                HealthAction.status == HealthActionStatus.TODO,
                HealthActionReminder.email_enabled.is_(True),
            )
            .order_by(HealthActionReminder.id)
        )
        return list(database_session.execute(statement).tuples())

    @staticmethod
    def create_pending_if_absent(
        database_session: Session,
        *,
        reminder_id: uuid.UUID,
        reminder_local_date: date,
        created_at: datetime,
    ) -> NotificationDelivery | None:
        statement = (
            postgresql_insert(NotificationDelivery)
            .values(
                reminder_id=reminder_id,
                channel=NotificationChannel.EMAIL,
                reminder_local_date=reminder_local_date,
                status=NotificationDeliveryStatus.PENDING,
                attempt_count=0,
                created_at=created_at,
                updated_at=created_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    NotificationDelivery.reminder_id,
                    NotificationDelivery.channel,
                    NotificationDelivery.reminder_local_date,
                ]
            )
            .returning(NotificationDelivery)
            .execution_options(populate_existing=True)
        )
        return database_session.scalars(statement).one_or_none()

    @staticmethod
    def list_for_reminder(
        database_session: Session,
        reminder_id: uuid.UUID,
    ) -> list[NotificationDelivery]:
        statement = (
            select(NotificationDelivery)
            .where(NotificationDelivery.reminder_id == reminder_id)
            .order_by(
                NotificationDelivery.reminder_local_date,
                NotificationDelivery.created_at,
                NotificationDelivery.id,
            )
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def get_by_id(
        database_session: Session,
        delivery_id: uuid.UUID,
    ) -> NotificationDelivery | None:
        return database_session.get(NotificationDelivery, delivery_id)

    @staticmethod
    def get_context(
        database_session: Session,
        delivery_id: uuid.UUID,
    ) -> tuple[NotificationDelivery, Account, HealthActionReminder, HealthAction] | None:
        statement = (
            select(NotificationDelivery, Account, HealthActionReminder, HealthAction)
            .join(
                HealthActionReminder,
                HealthActionReminder.id == NotificationDelivery.reminder_id,
            )
            .join(HealthAction, HealthAction.id == HealthActionReminder.action_id)
            .join(Person, Person.id == HealthAction.person_id)
            .join(Account, Account.id == Person.owner_account_id)
            .where(NotificationDelivery.id == delivery_id)
        )
        row = database_session.execute(statement).tuples().one_or_none()
        return row if row is not None else None

    @staticmethod
    def claim_next_pending(
        database_session: Session,
        *,
        claimed_at: datetime,
    ) -> NotificationDelivery | None:
        statement = (
            select(NotificationDelivery)
            .where(
                NotificationDelivery.channel == NotificationChannel.EMAIL,
                NotificationDelivery.status == NotificationDeliveryStatus.PENDING,
            )
            .order_by(NotificationDelivery.created_at, NotificationDelivery.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        delivery = database_session.scalar(statement)
        if delivery is None:
            return None
        delivery.status = NotificationDeliveryStatus.SENDING
        delivery.claimed_at = claimed_at
        delivery.attempt_count += 1
        delivery.updated_at = claimed_at
        database_session.flush()
        return delivery

    @staticmethod
    def list_stale_sending(
        database_session: Session,
        *,
        before: datetime,
    ) -> list[NotificationDelivery]:
        statement = (
            select(NotificationDelivery)
            .where(
                NotificationDelivery.channel == NotificationChannel.EMAIL,
                NotificationDelivery.status == NotificationDeliveryStatus.SENDING,
                NotificationDelivery.claimed_at.is_not(None),
                NotificationDelivery.claimed_at < before,
            )
            .with_for_update(skip_locked=True)
            .order_by(NotificationDelivery.claimed_at, NotificationDelivery.id)
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def mark_cancelled(
        database_session: Session,
        delivery: NotificationDelivery,
        *,
        updated_at: datetime,
    ) -> None:
        delivery.status = NotificationDeliveryStatus.CANCELLED
        delivery.updated_at = updated_at

    @staticmethod
    def mark_sent(
        database_session: Session,
        delivery: NotificationDelivery,
        *,
        sent_at: datetime,
    ) -> None:
        delivery.status = NotificationDeliveryStatus.SENT
        delivery.sent_at = sent_at
        delivery.updated_at = sent_at

    @staticmethod
    def mark_failed(
        database_session: Session,
        delivery: NotificationDelivery,
        *,
        failed_at: datetime,
        failure_code: str,
    ) -> None:
        delivery.status = NotificationDeliveryStatus.FAILED
        delivery.failed_at = failed_at
        delivery.failure_code = failure_code
        delivery.updated_at = failed_at

    @staticmethod
    def mark_unknown(
        database_session: Session,
        delivery: NotificationDelivery,
        *,
        updated_at: datetime,
        failure_code: str,
    ) -> None:
        delivery.status = NotificationDeliveryStatus.UNKNOWN
        delivery.failure_code = failure_code
        delivery.updated_at = updated_at


class HealthActionOutcomeRepository:
    @staticmethod
    def create_for_action(
        database_session: Session,
        action_id: uuid.UUID,
        *,
        note: str,
        observed_at: datetime,
    ) -> HealthActionOutcome:
        outcome = HealthActionOutcome(
            action_id=action_id,
            note=note,
            observed_at=observed_at,
        )
        database_session.add(outcome)
        return outcome

    @staticmethod
    def list_for_action(
        database_session: Session,
        action_id: uuid.UUID,
    ) -> list[HealthActionOutcome]:
        statement = (
            select(HealthActionOutcome)
            .where(HealthActionOutcome.action_id == action_id)
            .order_by(
                HealthActionOutcome.observed_at.desc(),
                HealthActionOutcome.created_at.desc(),
                HealthActionOutcome.id.desc(),
            )
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def get_for_action(
        database_session: Session,
        action_id: uuid.UUID,
        outcome_id: uuid.UUID,
    ) -> HealthActionOutcome | None:
        statement = select(HealthActionOutcome).where(
            HealthActionOutcome.id == outcome_id,
            HealthActionOutcome.action_id == action_id,
        )
        return database_session.scalar(statement)

    @staticmethod
    def list_since_for_person(
        database_session: Session,
        person_id: uuid.UUID,
        since: datetime,
    ) -> list[HealthActionOutcome]:
        statement = (
            select(HealthActionOutcome)
            .join(HealthAction, HealthAction.id == HealthActionOutcome.action_id)
            .where(
                HealthAction.person_id == person_id,
                HealthActionOutcome.observed_at >= since,
            )
            .order_by(
                HealthActionOutcome.observed_at.desc(),
                HealthActionOutcome.created_at.desc(),
                HealthActionOutcome.id.desc(),
            )
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def count_for_person(database_session: Session, person_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(HealthActionOutcome)
            .join(HealthAction, HealthAction.id == HealthActionOutcome.action_id)
            .where(HealthAction.person_id == person_id)
        )
        return database_session.scalar(statement) or 0


class HealthReportRepository:
    @staticmethod
    def find_by_sha256(
        database_session: Session,
        person_id: uuid.UUID,
        canonical_sha256: str,
    ) -> HealthReportModel | None:
        statement = select(HealthReportModel).where(
            HealthReportModel.person_id == person_id,
            HealthReportModel.canonical_sha256 == canonical_sha256,
        )
        return database_session.scalar(statement)

    @staticmethod
    def create_report(
        database_session: Session,
        person_id: uuid.UUID,
        *,
        schema_version: str,
        source_name: str,
        reported_at: datetime,
        canonical_sha256: str,
        observations: list[dict[str, Any]],
    ) -> HealthReportModel:
        report = HealthReportModel(
            person_id=person_id,
            schema_version=schema_version,
            source_name=source_name,
            reported_at=reported_at,
            canonical_sha256=canonical_sha256,
            status="pending",
        )
        database_session.add(report)
        database_session.flush()

        for obs_data in observations:
            obs = HealthReportObservationModel(
                report_id=report.id,
                person_id=person_id,
                code=obs_data["code"],
                display_name=obs_data["display_name"],
                value_numeric=obs_data.get("value_numeric"),
                value_text=obs_data.get("value_text"),
                unit=obs_data.get("unit"),
                reference_range=obs_data.get("reference_range"),
                observed_at=obs_data["observed_at"],
            )
            database_session.add(obs)

        return report

    @staticmethod
    def list_for_person(
        database_session: Session,
        person_id: uuid.UUID,
    ) -> list[HealthReportModel]:
        statement = (
            select(HealthReportModel)
            .where(HealthReportModel.person_id == person_id)
            .order_by(
                HealthReportModel.reported_at.desc(),
                HealthReportModel.created_at.desc(),
                HealthReportModel.id.desc(),
            )
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def get_for_person(
        database_session: Session,
        person_id: uuid.UUID,
        report_id: uuid.UUID,
    ) -> HealthReportModel | None:
        statement = select(HealthReportModel).where(
            HealthReportModel.id == report_id,
            HealthReportModel.person_id == person_id,
        )
        return database_session.scalar(statement)

    @staticmethod
    def confirm_report(
        database_session: Session,
        report: HealthReportModel,
        confirmed_at: datetime,
    ) -> HealthReportModel:
        if report.status != "confirmed":
            report.status = "confirmed"
            report.confirmed_at = confirmed_at
            database_session.add(report)
        return report

    @staticmethod
    def list_confirmed_observations_since_for_person(
        database_session: Session,
        person_id: uuid.UUID,
        since: datetime,
    ) -> list[HealthReportObservationModel]:
        statement = (
            select(HealthReportObservationModel)
            .join(HealthReportModel, HealthReportModel.id == HealthReportObservationModel.report_id)
            .where(
                HealthReportModel.person_id == person_id,
                HealthReportModel.status == "confirmed",
                HealthReportObservationModel.observed_at >= since,
            )
            .order_by(
                HealthReportObservationModel.observed_at.desc(),
                HealthReportObservationModel.created_at.desc(),
                HealthReportObservationModel.id.desc(),
            )
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def list_confirmed_observations_for_person(
        database_session: Session,
        person_id: uuid.UUID,
    ) -> list[HealthReportObservationModel]:
        statement = (
            select(HealthReportObservationModel)
            .join(HealthReportModel, HealthReportModel.id == HealthReportObservationModel.report_id)
            .where(
                HealthReportModel.person_id == person_id,
                HealthReportModel.status == "confirmed",
                HealthReportObservationModel.person_id == person_id,
            )
            .options(joinedload(HealthReportObservationModel.report))
            .order_by(
                HealthReportObservationModel.observed_at.desc(),
                HealthReportObservationModel.created_at.desc(),
                HealthReportObservationModel.id.desc(),
            )
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def count_for_person(database_session: Session, person_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(HealthReportModel)
            .where(HealthReportModel.person_id == person_id)
        )
        return database_session.scalar(statement) or 0
