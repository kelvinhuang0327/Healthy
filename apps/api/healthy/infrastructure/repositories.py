from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from healthy.domain.actions import HealthActionStatus
from healthy.infrastructure.models import (
    HealthAction,
    HealthActionOutcome,
    HealthMetric,
    HealthReportModel,
    HealthReportObservationModel,
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
        raw_json: str,
        observations: list[dict[str, Any]],
    ) -> HealthReportModel:
        report = HealthReportModel(
            person_id=person_id,
            schema_version=schema_version,
            source_name=source_name,
            reported_at=reported_at,
            canonical_sha256=canonical_sha256,
            status="pending",
            raw_json=raw_json,
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
    def count_for_person(database_session: Session, person_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(HealthReportModel)
            .where(HealthReportModel.person_id == person_id)
        )
        return database_session.scalar(statement) or 0
