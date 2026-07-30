from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from healthy.domain.actions import HealthActionStatus
from healthy.infrastructure.models import (
    HealthAction,
    HealthActionOutcome,
    HealthMetric,
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
        weight_kg: Decimal | None,
        blood_glucose_mg_dl: Decimal | None,
        note: str | None,
    ) -> HealthMetric:
        metric = HealthMetric(
            person_id=person_id,
            recorded_at=recorded_at,
            systolic_bp_mm_hg=systolic_bp_mm_hg,
            diastolic_bp_mm_hg=diastolic_bp_mm_hg,
            heart_rate_bpm=heart_rate_bpm,
            weight_kg=weight_kg,
            blood_glucose_mg_dl=blood_glucose_mg_dl,
            note=note,
        )
        database_session.add(metric)
        return metric

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
        note: str | None,
    ) -> SymptomLog:
        symptom_log = SymptomLog(
            person_id=person_id,
            symptom=symptom,
            occurred_at=occurred_at,
            severity=severity,
            duration_minutes=duration_minutes,
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
        )
        database_session.add(action)
        return action

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
