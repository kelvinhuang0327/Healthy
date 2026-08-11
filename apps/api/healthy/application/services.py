from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from healthy.application import health_score_inputs
from healthy.application.history import HistoryItem, build_history
from healthy.domain import actions as actions_domain
from healthy.domain import assistant as assistant_domain
from healthy.domain import insights as insights_domain
from healthy.domain import outcomes as outcomes_domain
from healthy.domain import reports as reports_domain
from healthy.domain.identity import AccountStatus, PersonRelationship, normalize_email
from healthy.infrastructure.models import (
    Account,
    HealthAction,
    HealthActionOutcome,
    HealthMetric,
    HealthReportModel,
    HealthReportObservationModel,
    Person,
    SessionRecord,
    SymptomLog,
)
from healthy.infrastructure.repositories import (
    HealthActionOutcomeRepository,
    HealthActionRepository,
    HealthMetricRepository,
    HealthReportRepository,
    PersonRepository,
    SymptomLogRepository,
)
from healthy.infrastructure.security import (
    hash_password,
    hash_session_token,
    new_session_credential,
    verify_password,
)


class DuplicateEmailError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class HealthMetricIntegrityError(Exception):
    pass


class SymptomLogIntegrityError(Exception):
    pass


class HealthActionIntegrityError(Exception):
    pass


class HealthActionOutcomeIntegrityError(Exception):
    pass


class HealthActionOutcomeInvalidStateError(Exception):
    pass


class HealthReportIntegrityError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    account: Account
    session: SessionRecord


@dataclass(frozen=True, slots=True)
class IssuedSession:
    account: Account
    session: SessionRecord
    raw_token: str
    default_person: Person | None = None


@dataclass(frozen=True, slots=True)
class AssistantToday:
    generated_at: datetime
    lookback_days: int
    latest_metric: HealthMetric | None
    recent_symptoms: list[SymptomLog]
    actions: list[HealthAction]
    recent_outcomes: list[HealthActionOutcome]
    daily_attention: tuple[assistant_domain.DailyAttentionItem, ...]
    recent_confirmed_observations: list[HealthReportObservationModel] = field(default_factory=list)
    insights: tuple[insights_domain.Insight, ...] = ()


def _session_record(account_id: uuid.UUID, max_age_seconds: int) -> tuple[SessionRecord, str]:
    raw_token, token_hash = new_session_credential()
    record = SessionRecord(
        account_id=account_id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(seconds=max_age_seconds),
    )
    return record, raw_token


def register_account(
    database_session: Session,
    *,
    email: str,
    password: str,
    display_name: str,
    session_max_age_seconds: int,
) -> IssuedSession:
    account = Account(
        normalized_email=normalize_email(email),
        password_hash=hash_password(password),
        status=AccountStatus.ACTIVE,
    )
    try:
        database_session.add(account)
        database_session.flush()
        default_person = Person(
            owner_account_id=account.id,
            display_name=display_name.strip(),
            relationship=PersonRelationship.SELF,
            is_default=True,
        )
        session_record, raw_token = _session_record(
            account.id,
            session_max_age_seconds,
        )
        database_session.add_all([default_person, session_record])
        database_session.commit()
    except IntegrityError as error:
        database_session.rollback()
        raise DuplicateEmailError from error
    except Exception:
        database_session.rollback()
        raise
    return IssuedSession(
        account=account,
        session=session_record,
        raw_token=raw_token,
        default_person=default_person,
    )


def login(
    database_session: Session,
    *,
    email: str,
    password: str,
    session_max_age_seconds: int,
) -> IssuedSession:
    statement = select(Account).where(
        Account.normalized_email == normalize_email(email),
        Account.status == AccountStatus.ACTIVE,
    )
    account = database_session.scalar(statement)
    valid_password = verify_password(password, account.password_hash if account else None)
    if account is None or not valid_password:
        raise InvalidCredentialsError
    session_record, raw_token = _session_record(account.id, session_max_age_seconds)
    database_session.add(session_record)
    database_session.commit()
    return IssuedSession(account=account, session=session_record, raw_token=raw_token)


def resolve_session(
    database_session: Session,
    *,
    raw_token: str,
) -> AuthenticatedSession | None:
    statement = (
        select(SessionRecord, Account)
        .join(Account, Account.id == SessionRecord.account_id)
        .where(
            SessionRecord.token_hash == hash_session_token(raw_token),
            SessionRecord.revoked_at.is_(None),
            SessionRecord.expires_at > datetime.now(UTC),
            Account.status == AccountStatus.ACTIVE,
        )
    )
    row = database_session.execute(statement).one_or_none()
    if row is None:
        return None
    session_record, account = row
    return AuthenticatedSession(account=account, session=session_record)


def revoke_session(database_session: Session, authenticated: AuthenticatedSession) -> None:
    authenticated.session.revoked_at = datetime.now(UTC)
    database_session.commit()


def list_persons(database_session: Session, owner_account_id: uuid.UUID) -> list[Person]:
    return PersonRepository.list_for_owner(database_session, owner_account_id)


def get_person(
    database_session: Session,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
) -> Person | None:
    return PersonRepository.get_for_owner(database_session, owner_account_id, person_id)


def create_person(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    display_name: str,
    relationship: str,
) -> Person:
    person = PersonRepository.create_non_default(
        database_session,
        owner_account_id,
        display_name.strip(),
        relationship,
    )
    database_session.commit()
    return person


def update_person_height(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    height_cm: Decimal | None,
) -> Person | None:
    person = PersonRepository.update_height_for_owner(
        database_session,
        owner_account_id,
        person_id,
        height_cm,
    )
    if person is None:
        return None
    database_session.commit()
    return person


def create_health_metric(
    database_session: Session,
    *,
    person_id: uuid.UUID,
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
    metric = HealthMetricRepository.create_for_person(
        database_session,
        person_id,
        recorded_at=recorded_at,
        systolic_bp_mm_hg=systolic_bp_mm_hg,
        diastolic_bp_mm_hg=diastolic_bp_mm_hg,
        heart_rate_bpm=heart_rate_bpm,
        steps=steps,
        weight_kg=weight_kg,
        blood_glucose_mg_dl=blood_glucose_mg_dl,
        sleep_hours=sleep_hours,
        note=note,
    )
    try:
        database_session.commit()
    except IntegrityError as error:
        database_session.rollback()
        raise HealthMetricIntegrityError from error
    return metric


def list_health_metrics(database_session: Session, person_id: uuid.UUID) -> list[HealthMetric]:
    return HealthMetricRepository.list_for_person(database_session, person_id)


def get_health_metric(
    database_session: Session,
    person_id: uuid.UUID,
    metric_id: uuid.UUID,
) -> HealthMetric | None:
    return HealthMetricRepository.get_for_person(database_session, person_id, metric_id)


def create_symptom_log(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    symptom: str,
    occurred_at: datetime,
    severity: int,
    duration_minutes: int | None,
    note: str | None,
    estimated_start_date: date | None = None,
    estimated_duration_days: int | None = None,
) -> SymptomLog | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    symptom_log = SymptomLogRepository.create_for_person(
        database_session,
        person.id,
        symptom=symptom,
        occurred_at=occurred_at,
        severity=severity,
        duration_minutes=duration_minutes,
        estimated_start_date=estimated_start_date,
        estimated_duration_days=estimated_duration_days,
        note=note,
    )
    try:
        database_session.commit()
    except IntegrityError as error:
        database_session.rollback()
        raise SymptomLogIntegrityError from error
    return symptom_log


def list_symptom_logs(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
) -> list[SymptomLog] | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    return SymptomLogRepository.list_for_person(database_session, person.id)


def get_symptom_log(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    symptom_id: uuid.UUID,
) -> SymptomLog | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    return SymptomLogRepository.get_for_person(database_session, person.id, symptom_id)


def create_health_action(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    title: str,
    description: str | None,
    due_at: datetime | None,
) -> HealthAction | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    action = HealthActionRepository.create_for_person(
        database_session,
        person.id,
        title=actions_domain.normalize_title(title),
        description=actions_domain.normalize_description(description),
        due_at=due_at,
    )
    try:
        database_session.commit()
    except IntegrityError as error:
        database_session.rollback()
        raise HealthActionIntegrityError from error
    return action


def list_health_actions(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
) -> list[HealthAction] | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    return HealthActionRepository.list_for_person(database_session, person.id)


def get_health_action(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    action_id: uuid.UUID,
) -> HealthAction | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    return HealthActionRepository.get_for_person(database_session, person.id, action_id)


def complete_health_action(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    action_id: uuid.UUID,
) -> HealthAction | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    try:
        action = HealthActionRepository.complete_for_person(
            database_session,
            person.id,
            action_id,
            datetime.now(UTC),
        )
        if action is not None and action.status == actions_domain.HealthActionStatus.DONE:
            database_session.commit()
    except IntegrityError as error:
        database_session.rollback()
        raise HealthActionIntegrityError from error
    return action


def create_health_action_outcome(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    action_id: uuid.UUID,
    note: str,
    observed_at: datetime,
) -> HealthActionOutcome | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    action = HealthActionRepository.get_for_person(database_session, person.id, action_id)
    if action is None:
        return None
    if action.status != actions_domain.HealthActionStatus.DONE:
        raise HealthActionOutcomeInvalidStateError
    outcome = HealthActionOutcomeRepository.create_for_action(
        database_session,
        action.id,
        note=outcomes_domain.normalize_note(note),
        observed_at=observed_at,
    )
    try:
        database_session.commit()
    except IntegrityError as error:
        database_session.rollback()
        raise HealthActionOutcomeIntegrityError from error
    return outcome


def list_health_action_outcomes(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    action_id: uuid.UUID,
) -> list[HealthActionOutcome] | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    action = HealthActionRepository.get_for_person(database_session, person.id, action_id)
    if action is None:
        return None
    return HealthActionOutcomeRepository.list_for_action(database_session, action.id)


def get_health_action_outcome(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    action_id: uuid.UUID,
    outcome_id: uuid.UUID,
) -> HealthActionOutcome | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    action = HealthActionRepository.get_for_person(database_session, person.id, action_id)
    if action is None:
        return None
    return HealthActionOutcomeRepository.get_for_action(
        database_session,
        action.id,
        outcome_id,
    )


def get_assistant_today(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    now: datetime,
    lookback: timedelta = assistant_domain.DEFAULT_LOOKBACK,
) -> AssistantToday | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    since = now - lookback

    latest_metric = HealthMetricRepository.get_latest_for_person(database_session, person.id)
    recent_symptoms = SymptomLogRepository.list_since_for_person(database_session, person.id, since)
    actions = HealthActionRepository.list_open_or_recently_completed_for_person(
        database_session,
        person.id,
        since,
    )
    recent_outcomes = HealthActionOutcomeRepository.list_since_for_person(
        database_session,
        person.id,
        since,
    )
    recent_confirmed_obs = HealthReportRepository.list_confirmed_observations_since_for_person(
        database_session,
        person.id,
        since,
    )
    all_metrics = HealthMetricRepository.list_for_person(database_session, person.id)
    all_symptoms = SymptomLogRepository.list_for_person(database_session, person.id)
    all_confirmed_obs = HealthReportRepository.list_confirmed_observations_for_person(
        database_session,
        person.id,
    )
    open_actions = [
        action for action in actions if action.status == actions_domain.HealthActionStatus.TODO
    ]
    metric_is_recent = latest_metric is not None and latest_metric.recorded_at >= since

    daily_attention = assistant_domain.evaluate_daily_attention(
        now=now,
        lookback=lookback,
        all_time_metric_count=HealthMetricRepository.count_for_person(database_session, person.id),
        all_time_symptom_count=SymptomLogRepository.count_for_person(database_session, person.id),
        all_time_action_count=HealthActionRepository.count_for_person(database_session, person.id),
        all_time_outcome_count=HealthActionOutcomeRepository.count_for_person(
            database_session,
            person.id,
        ),
        all_time_report_count=HealthReportRepository.count_for_person(database_session, person.id),
        recent_metrics=(
            [
                assistant_domain.MetricSnapshot(
                    id=latest_metric.id, recorded_at=latest_metric.recorded_at
                )
            ]
            if metric_is_recent and latest_metric is not None
            else []
        ),
        recent_symptoms=[
            assistant_domain.SymptomSnapshot(
                id=symptom.id,
                symptom=symptom.symptom,
                occurred_at=symptom.occurred_at,
            )
            for symptom in recent_symptoms
        ],
        open_actions=[
            assistant_domain.ActionSnapshot(
                id=action.id,
                title=action.title,
                status=actions_domain.HealthActionStatus(action.status),
                due_at=action.due_at,
                completed_at=action.completed_at,
            )
            for action in open_actions
        ],
        recent_outcomes=[
            assistant_domain.OutcomeSnapshot(
                id=outcome.id,
                action_id=outcome.action_id,
                observed_at=outcome.observed_at,
            )
            for outcome in recent_outcomes
        ],
        recent_confirmed_observations=[
            assistant_domain.ReportObservationSnapshot(
                id=obs.id,
                report_id=obs.report_id,
                code=obs.code,
                display_name=obs.display_name,
                observed_at=obs.observed_at,
            )
            for obs in recent_confirmed_obs
        ],
    )
    insights = insights_domain.build_insights(
        metrics=[
            insights_domain.MetricSnapshot(
                id=metric.id,
                recorded_at=metric.recorded_at,
                systolic_bp_mm_hg=metric.systolic_bp_mm_hg,
                diastolic_bp_mm_hg=metric.diastolic_bp_mm_hg,
                heart_rate_bpm=metric.heart_rate_bpm,
                weight_kg=metric.weight_kg,
                blood_glucose_mg_dl=metric.blood_glucose_mg_dl,
            )
            for metric in all_metrics
        ],
        symptoms=[
            insights_domain.SymptomSnapshot(
                id=symptom.id,
                symptom=symptom.symptom,
                occurred_at=symptom.occurred_at,
            )
            for symptom in all_symptoms
        ],
        confirmed_report_observations=[
            insights_domain.ReportObservationSnapshot(
                id=observation.id,
                report_id=observation.report_id,
                report_source_name=observation.report.source_name,
                code=observation.code,
                display_name=observation.display_name,
                value_numeric=observation.value_numeric,
                value_text=observation.value_text,
                unit=observation.unit,
                observed_at=observation.observed_at,
                created_at=observation.created_at,
            )
            for observation in all_confirmed_obs
        ],
    )

    return AssistantToday(
        generated_at=now,
        lookback_days=lookback.days,
        latest_metric=latest_metric,
        recent_symptoms=recent_symptoms,
        actions=actions,
        recent_outcomes=recent_outcomes,
        daily_attention=daily_attention,
        recent_confirmed_observations=recent_confirmed_obs,
        insights=insights,
    )


def get_health_history(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
) -> list[HistoryItem] | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    return build_history(
        HealthMetricRepository.list_for_person(database_session, person.id),
        SymptomLogRepository.list_for_person(database_session, person.id),
        HealthReportRepository.list_confirmed_observations_for_person(
            database_session,
            person.id,
        ),
    )


def get_health_score_inputs(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    now: datetime,
    lookback_days: int = health_score_inputs.LEGACY_LAB_LOOKBACK_DAYS,
) -> health_score_inputs.HealthScoreInputs | None:
    """Read the current person's reusable score inputs without writing state."""
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    metrics = HealthMetricRepository.list_for_person(database_session, person.id)
    symptoms = SymptomLogRepository.list_for_person(database_session, person.id)
    observations = HealthReportRepository.list_confirmed_observations_for_person(
        database_session,
        person.id,
    )
    return health_score_inputs.build_health_score_inputs(
        observations,
        person_id=person.id,
        now=now,
        lookback_days=lookback_days,
        metrics=metrics,
        symptoms=symptoms,
        height_cm=person.height_cm,
    )


def import_health_report(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    raw_data: Any,
) -> tuple[HealthReportModel, bool] | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None

    canonical_dict, sha256_hash = reports_domain.canonicalize_and_validate_report_json(raw_data)

    existing = HealthReportRepository.find_by_sha256(database_session, person.id, sha256_hash)
    if existing is not None:
        return existing, True

    try:
        obs_data_list = []
        for obs in canonical_dict["observations"]:
            obs_data_list.append(
                {
                    "code": obs["code"],
                    "display_name": obs["display_name"],
                    "value_numeric": obs.get("value_numeric"),
                    "value_text": obs.get("value_text"),
                    "unit": obs.get("unit"),
                    "reference_range": obs.get("reference_range"),
                    "observed_at": datetime.fromisoformat(obs["observed_at"]),
                }
            )
        report = HealthReportRepository.create_report(
            database_session,
            person.id,
            schema_version=canonical_dict["schema_version"],
            source_name=canonical_dict["source_name"],
            reported_at=datetime.fromisoformat(canonical_dict["reported_at"]),
            canonical_sha256=sha256_hash,
            observations=obs_data_list,
        )
        database_session.commit()
        return report, False
    except IntegrityError as exc:
        database_session.rollback()
        raise HealthReportIntegrityError(
            "Failed to store health report due to database integrity constraints."
        ) from exc


def list_health_reports(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
) -> list[HealthReportModel] | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    return HealthReportRepository.list_for_person(database_session, person.id)


def get_health_report(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    report_id: uuid.UUID,
) -> HealthReportModel | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    return HealthReportRepository.get_for_person(database_session, person.id, report_id)


def confirm_health_report(
    database_session: Session,
    *,
    owner_account_id: uuid.UUID,
    person_id: uuid.UUID,
    report_id: uuid.UUID,
    now: datetime,
) -> HealthReportModel | None:
    person = PersonRepository.get_for_owner(database_session, owner_account_id, person_id)
    if person is None:
        return None
    report = HealthReportRepository.get_for_person(database_session, person.id, report_id)
    if report is None:
        return None
    confirmed_report = HealthReportRepository.confirm_report(database_session, report, now)
    database_session.commit()
    return confirmed_report
