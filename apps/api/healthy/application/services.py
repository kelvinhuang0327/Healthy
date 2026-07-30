from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from healthy.domain import actions as actions_domain
from healthy.domain import outcomes as outcomes_domain
from healthy.domain.identity import AccountStatus, PersonRelationship, normalize_email
from healthy.infrastructure.models import (
    Account,
    HealthAction,
    HealthActionOutcome,
    HealthMetric,
    Person,
    SessionRecord,
    SymptomLog,
)
from healthy.infrastructure.repositories import (
    HealthActionOutcomeRepository,
    HealthActionRepository,
    HealthMetricRepository,
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


def create_health_metric(
    database_session: Session,
    *,
    person_id: uuid.UUID,
    recorded_at: datetime,
    systolic_bp_mm_hg: int | None,
    diastolic_bp_mm_hg: int | None,
    heart_rate_bpm: int | None,
    weight_kg: Decimal | None,
    blood_glucose_mg_dl: Decimal | None,
    note: str | None,
) -> HealthMetric:
    metric = HealthMetricRepository.create_for_person(
        database_session,
        person_id,
        recorded_at=recorded_at,
        systolic_bp_mm_hg=systolic_bp_mm_hg,
        diastolic_bp_mm_hg=diastolic_bp_mm_hg,
        heart_rate_bpm=heart_rate_bpm,
        weight_kg=weight_kg,
        blood_glucose_mg_dl=blood_glucose_mg_dl,
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
