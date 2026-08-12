from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from healthy.application import services
from healthy.application.services import AuthenticatedSession
from healthy.domain import reports as reports_domain
from healthy.infrastructure.config import Settings
from healthy.infrastructure.models import Person
from healthy.presentation.dependencies import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    get_authenticated_session,
    get_command_session,
    get_database_session,
    get_settings,
    require_origin,
)
from healthy.presentation.schemas import (
    AccountCreate,
    AccountSummary,
    AssistantTodaySummary,
    DailyAttentionItemSummary,
    HealthActionCreate,
    HealthActionOutcomeCreate,
    HealthActionOutcomeSummary,
    HealthActionSummary,
    HealthAnalyticsMetricSummary,
    HealthAnalyticsSummary,
    HealthHistoryItemSummary,
    HealthMetricCreate,
    HealthMetricSummary,
    HealthReportDetail,
    HealthReportSummary,
    HealthScoreComponentSummary,
    HealthScoreCoverageSummary,
    HealthScoreSummary,
    HistorySourceSummary,
    InsightEvidenceSummary,
    InsightSummary,
    PersonCreate,
    PersonHeightUpdate,
    PersonSummary,
    RegistrationResponse,
    RiskAlertEvidenceSummary,
    RiskAlertsSummary,
    RiskAlertSummary,
    SessionCreate,
    SessionSummary,
    SymptomLogCreate,
    SymptomLogSummary,
)

router = APIRouter(prefix="/v1")


def _session_summary(issued: services.IssuedSession) -> SessionSummary:
    return SessionSummary(
        id=issued.session.id,
        account=AccountSummary.model_validate(issued.account),
        expires_at=issued.session.expires_at,
    )


def _set_authentication_cookies(
    response: Response,
    issued: services.IssuedSession,
    settings: Settings,
) -> None:
    from healthy.infrastructure.security import create_csrf_token

    csrf_token = create_csrf_token(issued.session.id, settings.csrf_secret)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=issued.raw_token,
        max_age=settings.session_max_age_seconds,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        key=CSRF_COOKIE,
        value=csrf_token,
        max_age=settings.session_max_age_seconds,
        path="/",
        secure=settings.cookie_secure,
        httponly=False,
        samesite="lax",
    )


@router.post(
    "/accounts",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_origin)],
)
def register_account(
    payload: AccountCreate,
    response: Response,
    database_session: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RegistrationResponse:
    try:
        issued = services.register_account(
            database_session,
            email=str(payload.email),
            password=payload.password,
            display_name=payload.display_name,
            session_max_age_seconds=settings.session_max_age_seconds,
        )
    except services.DuplicateEmailError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Unable to create account",
        ) from error
    _set_authentication_cookies(response, issued, settings)
    if issued.default_person is None:
        raise RuntimeError("Registration invariant violated")
    return RegistrationResponse(
        account=AccountSummary.model_validate(issued.account),
        default_person=PersonSummary.model_validate(issued.default_person),
        session=_session_summary(issued),
    )


@router.post(
    "/sessions",
    response_model=SessionSummary,
    dependencies=[Depends(require_origin)],
)
def create_session(
    payload: SessionCreate,
    response: Response,
    database_session: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SessionSummary:
    try:
        issued = services.login(
            database_session,
            email=str(payload.email),
            password=payload.password,
            session_max_age_seconds=settings.session_max_age_seconds,
        )
    except services.InvalidCredentialsError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        ) from error
    _set_authentication_cookies(response, issued, settings)
    return _session_summary(issued)


@router.delete(
    "/sessions/current",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def delete_current_session(
    response: Response,
    authenticated: Annotated[AuthenticatedSession, Depends(get_command_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    services.revoke_session(database_session, authenticated)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        CSRF_COOKIE,
        path="/",
        secure=settings.cookie_secure,
        httponly=False,
        samesite="lax",
    )


@router.get("/session", response_model=SessionSummary)
def get_current_session(
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
) -> SessionSummary:
    return SessionSummary(
        id=authenticated.session.id,
        account=AccountSummary.model_validate(authenticated.account),
        expires_at=authenticated.session.expires_at,
    )


@router.get("/persons", response_model=list[PersonSummary])
def get_persons(
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> list[PersonSummary]:
    persons = services.list_persons(database_session, authenticated.account.id)
    return [PersonSummary.model_validate(person) for person in persons]


@router.post("/persons", response_model=PersonSummary, status_code=status.HTTP_201_CREATED)
def post_person(
    payload: PersonCreate,
    authenticated: Annotated[AuthenticatedSession, Depends(get_command_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> PersonSummary:
    person = services.create_person(
        database_session,
        owner_account_id=authenticated.account.id,
        display_name=payload.display_name,
        relationship=payload.relationship,
    )
    return PersonSummary.model_validate(person)


@router.get("/persons/{person_id}", response_model=PersonSummary)
def get_person(
    person_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> PersonSummary:
    person = services.get_person(
        database_session,
        authenticated.account.id,
        person_id,
    )
    if person is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return PersonSummary.model_validate(person)


@router.patch("/persons/{person_id}/profile", response_model=PersonSummary)
def patch_person_profile(
    person_id: uuid.UUID,
    payload: PersonHeightUpdate,
    authenticated: Annotated[AuthenticatedSession, Depends(get_command_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> PersonSummary:
    person = services.update_person_height(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
        height_cm=payload.height_cm,
    )
    if person is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return PersonSummary.model_validate(person)


def _get_owned_person(
    person_id: uuid.UUID,
    authenticated: AuthenticatedSession,
    database_session: Session,
) -> Person:
    person = services.get_person(database_session, authenticated.account.id, person_id)
    if person is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return person


@router.post(
    "/persons/{person_id}/metrics",
    response_model=HealthMetricSummary,
    status_code=status.HTTP_201_CREATED,
)
def post_health_metric(
    person_id: uuid.UUID,
    payload: HealthMetricCreate,
    authenticated: Annotated[AuthenticatedSession, Depends(get_command_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> HealthMetricSummary:
    person = _get_owned_person(person_id, authenticated, database_session)
    try:
        metric = services.create_health_metric(
            database_session,
            person_id=person.id,
            recorded_at=payload.recorded_at,
            systolic_bp_mm_hg=payload.systolic_bp_mm_hg,
            diastolic_bp_mm_hg=payload.diastolic_bp_mm_hg,
            heart_rate_bpm=payload.heart_rate_bpm,
            steps=payload.steps,
            weight_kg=payload.weight_kg,
            blood_glucose_mg_dl=payload.blood_glucose_mg_dl,
            sleep_hours=payload.sleep_hours,
            note=payload.note,
        )
    except services.HealthMetricIntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid request",
        ) from error
    return HealthMetricSummary.model_validate(metric)


@router.get("/persons/{person_id}/metrics", response_model=list[HealthMetricSummary])
def get_health_metrics(
    person_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> list[HealthMetricSummary]:
    _get_owned_person(person_id, authenticated, database_session)
    metrics = services.list_health_metrics(database_session, person_id)
    return [HealthMetricSummary.model_validate(metric) for metric in metrics]


@router.get(
    "/persons/{person_id}/metrics/{metric_id}",
    response_model=HealthMetricSummary,
)
def get_health_metric(
    person_id: uuid.UUID,
    metric_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> HealthMetricSummary:
    _get_owned_person(person_id, authenticated, database_session)
    metric = services.get_health_metric(database_session, person_id, metric_id)
    if metric is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metric not found",
        )
    return HealthMetricSummary.model_validate(metric)


@router.get(
    "/persons/{person_id}/health-score",
    response_model=HealthScoreSummary,
)
def get_health_score(
    person_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> HealthScoreSummary:
    result = services.get_health_score(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return HealthScoreSummary(
        score=result.score,
        status=result.status,
        rule_version=result.rule_version,
        anchor_at=result.anchor_at,
        data_points=result.data_points,
        components=[
            HealthScoreComponentSummary(
                kind=component.kind,
                label=component.label,
                points=component.points,
                penalty=component.penalty,
                evidence_ids=list(component.evidence_ids),
                rationale=component.rationale,
            )
            for component in result.components
        ],
        coverage=HealthScoreCoverageSummary(
            evaluated_inputs=list(result.coverage.evaluated_inputs),
            missing_inputs=list(result.coverage.missing_inputs),
            unsupported_sources=list(result.coverage.unsupported_sources),
        ),
        limitations=result.limitations,
    )


@router.get(
    "/persons/{person_id}/risk-alerts",
    response_model=RiskAlertsSummary,
)
def get_risk_alerts(
    person_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> RiskAlertsSummary:
    result = services.get_risk_alerts(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return RiskAlertsSummary(
        active_count=result.active_count,
        alerts=[
            RiskAlertSummary(
                rule_code=alert.rule_code,
                risk_type=alert.risk_type,
                severity=alert.severity,
                status=alert.status,
                evidence=RiskAlertEvidenceSummary(
                    source_kind=alert.evidence.source_kind,
                    source_id=alert.evidence.source_id,
                    person_id=alert.evidence.person_id,
                    observed_at=alert.evidence.observed_at,
                    observation_id=alert.evidence.observation_id,
                    report_id=alert.evidence.report_id,
                    report_source_name=alert.evidence.report_source_name,
                ),
            )
            for alert in result.alerts
        ],
    )


@router.post(
    "/persons/{person_id}/symptoms",
    response_model=SymptomLogSummary,
    status_code=status.HTTP_201_CREATED,
)
def post_symptom_log(
    person_id: uuid.UUID,
    payload: SymptomLogCreate,
    authenticated: Annotated[AuthenticatedSession, Depends(get_command_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> SymptomLogSummary:
    try:
        symptom_log = services.create_symptom_log(
            database_session,
            owner_account_id=authenticated.account.id,
            person_id=person_id,
            symptom=payload.symptom,
            occurred_at=payload.occurred_at,
            severity=payload.severity,
            duration_minutes=payload.duration_minutes,
            estimated_start_date=payload.estimated_start_date,
            estimated_duration_days=payload.estimated_duration_days,
            note=payload.note,
        )
    except services.SymptomLogIntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid request",
        ) from error
    if symptom_log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return SymptomLogSummary.model_validate(symptom_log)


@router.get("/persons/{person_id}/symptoms", response_model=list[SymptomLogSummary])
def get_symptom_logs(
    person_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> list[SymptomLogSummary]:
    symptom_logs = services.list_symptom_logs(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
    )
    if symptom_logs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return [SymptomLogSummary.model_validate(symptom_log) for symptom_log in symptom_logs]


@router.get(
    "/persons/{person_id}/symptoms/{symptom_id}",
    response_model=SymptomLogSummary,
)
def get_symptom_log(
    person_id: uuid.UUID,
    symptom_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> SymptomLogSummary:
    symptom_log = services.get_symptom_log(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
        symptom_id=symptom_id,
    )
    if symptom_log is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Symptom record not found",
        )
    return SymptomLogSummary.model_validate(symptom_log)


@router.post(
    "/persons/{person_id}/actions",
    response_model=HealthActionSummary,
    status_code=status.HTTP_201_CREATED,
)
def post_health_action(
    person_id: uuid.UUID,
    payload: HealthActionCreate,
    authenticated: Annotated[AuthenticatedSession, Depends(get_command_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> HealthActionSummary:
    try:
        action = services.create_health_action(
            database_session,
            owner_account_id=authenticated.account.id,
            person_id=person_id,
            title=payload.title,
            description=payload.description,
            due_at=payload.due_at,
        )
    except services.HealthActionIntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid request",
        ) from error
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return HealthActionSummary.model_validate(action)


@router.get("/persons/{person_id}/actions", response_model=list[HealthActionSummary])
def get_health_actions(
    person_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> list[HealthActionSummary]:
    actions = services.list_health_actions(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
    )
    if actions is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return [HealthActionSummary.model_validate(action) for action in actions]


@router.get(
    "/persons/{person_id}/actions/{action_id}",
    response_model=HealthActionSummary,
)
def get_health_action(
    person_id: uuid.UUID,
    action_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> HealthActionSummary:
    _get_owned_person(person_id, authenticated, database_session)
    action = services.get_health_action(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
        action_id=action_id,
    )
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action not found",
        )
    return HealthActionSummary.model_validate(action)


@router.post(
    "/persons/{person_id}/actions/{action_id}/complete",
    response_model=HealthActionSummary,
)
def complete_health_action(
    person_id: uuid.UUID,
    action_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_command_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> HealthActionSummary:
    _get_owned_person(person_id, authenticated, database_session)
    try:
        action = services.complete_health_action(
            database_session,
            owner_account_id=authenticated.account.id,
            person_id=person_id,
            action_id=action_id,
        )
    except services.HealthActionIntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid request",
        ) from error
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action not found",
        )
    return HealthActionSummary.model_validate(action)


@router.post(
    "/persons/{person_id}/actions/{action_id}/outcomes",
    response_model=HealthActionOutcomeSummary,
    status_code=status.HTTP_201_CREATED,
)
def post_health_action_outcome(
    person_id: uuid.UUID,
    action_id: uuid.UUID,
    payload: HealthActionOutcomeCreate,
    authenticated: Annotated[AuthenticatedSession, Depends(get_command_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> HealthActionOutcomeSummary:
    _get_owned_person(person_id, authenticated, database_session)
    try:
        outcome = services.create_health_action_outcome(
            database_session,
            owner_account_id=authenticated.account.id,
            person_id=person_id,
            action_id=action_id,
            note=payload.note,
            observed_at=payload.observed_at,
        )
    except (
        services.HealthActionOutcomeIntegrityError,
        services.HealthActionOutcomeInvalidStateError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid request",
        ) from error
    if outcome is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action not found",
        )
    return HealthActionOutcomeSummary.model_validate(outcome)


@router.get(
    "/persons/{person_id}/actions/{action_id}/outcomes",
    response_model=list[HealthActionOutcomeSummary],
)
def get_health_action_outcomes(
    person_id: uuid.UUID,
    action_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> list[HealthActionOutcomeSummary]:
    _get_owned_person(person_id, authenticated, database_session)
    outcomes = services.list_health_action_outcomes(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
        action_id=action_id,
    )
    if outcomes is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action not found",
        )
    return [HealthActionOutcomeSummary.model_validate(outcome) for outcome in outcomes]


@router.get(
    "/persons/{person_id}/actions/{action_id}/outcomes/{outcome_id}",
    response_model=HealthActionOutcomeSummary,
)
def get_health_action_outcome(
    person_id: uuid.UUID,
    action_id: uuid.UUID,
    outcome_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> HealthActionOutcomeSummary:
    _get_owned_person(person_id, authenticated, database_session)
    action = services.get_health_action(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
        action_id=action_id,
    )
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action not found",
        )
    outcome = services.get_health_action_outcome(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
        action_id=action.id,
        outcome_id=outcome_id,
    )
    if outcome is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Outcome not found",
        )
    return HealthActionOutcomeSummary.model_validate(outcome)


@router.get(
    "/persons/{person_id}/assistant/today",
    response_model=AssistantTodaySummary,
)
def get_assistant_today(
    person_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> AssistantTodaySummary:
    result = services.get_assistant_today(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
        now=datetime.now(UTC),
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return AssistantTodaySummary(
        generated_at=result.generated_at,
        lookback_days=result.lookback_days,
        latest_metric=(
            HealthMetricSummary.model_validate(result.latest_metric)
            if result.latest_metric is not None
            else None
        ),
        recent_symptoms=[
            SymptomLogSummary.model_validate(symptom) for symptom in result.recent_symptoms
        ],
        open_or_recent_actions=[
            HealthActionSummary.model_validate(action) for action in result.actions
        ],
        recent_outcomes=[
            HealthActionOutcomeSummary.model_validate(outcome) for outcome in result.recent_outcomes
        ],
        daily_attention=[
            DailyAttentionItemSummary(
                kind=item.kind,
                title=item.title,
                rationale=item.rationale,
                evidence_ids=list(item.evidence_ids),
                confidence=item.confidence,
                limitations=item.limitations,
                rule_version=item.rule_version,
            )
            for item in result.daily_attention
        ],
        insights=[
            InsightSummary(
                id=insight.id,
                insight_type=insight.insight_type,
                headline=insight.headline,
                observed_at=insight.observed_at,
                evidence=[
                    InsightEvidenceSummary(
                        source_kind=evidence.source_kind,
                        source_record_id=evidence.source_record_id,
                        occurred_at=evidence.occurred_at,
                        role=evidence.role,
                        report_id=evidence.report_id,
                        report_source_name=evidence.report_source_name,
                    )
                    for evidence in insight.evidence
                ],
            )
            for insight in result.insights
        ],
    )


@router.get(
    "/persons/{person_id}/history",
    response_model=list[HealthHistoryItemSummary],
)
def get_health_history(
    person_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> list[HealthHistoryItemSummary]:
    history = services.get_health_history(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
    )
    if history is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return [
        HealthHistoryItemSummary(
            id=item.id,
            kind=item.kind,
            occurred_at=item.occurred_at,
            title=item.title,
            primary_value=item.primary_value,
            unit=item.unit,
            detail=item.detail,
            source=HistorySourceSummary(
                type=item.source.type,
                id=item.source.id,
                report_id=item.source.report_id,
                report_source_name=item.source.report_source_name,
            ),
        )
        for item in history
    ]


@router.get(
    "/persons/{person_id}/analytics",
    response_model=HealthAnalyticsSummary,
)
def get_health_analytics(
    person_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
    days: Annotated[int, Query(ge=7, le=365)] = 90,
) -> HealthAnalyticsSummary:
    result = services.get_health_analytics(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
        now=datetime.now(UTC),
        period_days=days,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return HealthAnalyticsSummary(
        period_days=result.period_days,
        summaries=[
            HealthAnalyticsMetricSummary(
                metric=summary.metric,
                label=summary.label,
                unit=summary.unit,
                points=summary.points,
                first_value=summary.first_value,
                last_value=summary.last_value,
                change_percent=summary.change_percent,
                slope_per_day=summary.slope_per_day,
                direction=summary.direction,
            )
            for summary in result.summaries
        ],
    )


@router.post(
    "/persons/{person_id}/reports",
    response_model=HealthReportDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_origin)],
)
def import_health_report(
    person_id: uuid.UUID,
    payload: Annotated[dict[str, Any], Body(...)],
    response: Response,
    authenticated: Annotated[AuthenticatedSession, Depends(get_command_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> HealthReportDetail:
    try:
        result = services.import_health_report(
            database_session,
            owner_account_id=authenticated.account.id,
            person_id=person_id,
            raw_data=payload,
        )
    except reports_domain.InvalidReportSchemaError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except services.HealthReportIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Health report data violated system integrity rules.",
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )

    report, is_duplicate = result
    if is_duplicate:
        response.status_code = status.HTTP_200_OK

    return HealthReportDetail.model_validate(report)


@router.get(
    "/persons/{person_id}/reports",
    response_model=list[HealthReportSummary],
)
def list_health_reports(
    person_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> list[HealthReportSummary]:
    reports = services.list_health_reports(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
    )
    if reports is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Person not found",
        )
    return [HealthReportSummary.model_validate(report) for report in reports]


@router.get(
    "/persons/{person_id}/reports/{report_id}",
    response_model=HealthReportDetail,
)
def get_health_report(
    person_id: uuid.UUID,
    report_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> HealthReportDetail:
    report = services.get_health_report(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
        report_id=report_id,
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found",
        )
    return HealthReportDetail.model_validate(report)


@router.post(
    "/persons/{person_id}/reports/{report_id}/confirm",
    response_model=HealthReportDetail,
    dependencies=[Depends(require_origin)],
)
def confirm_health_report(
    person_id: uuid.UUID,
    report_id: uuid.UUID,
    authenticated: Annotated[AuthenticatedSession, Depends(get_command_session)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> HealthReportDetail:
    report = services.confirm_health_report(
        database_session,
        owner_account_id=authenticated.account.id,
        person_id=person_id,
        report_id=report_id,
        now=datetime.now(UTC),
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found",
        )
    return HealthReportDetail.model_validate(report)
