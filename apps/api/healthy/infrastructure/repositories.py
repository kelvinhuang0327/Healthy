from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthy.infrastructure.models import HealthMetric, Person


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
