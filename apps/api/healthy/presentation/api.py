from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from healthy.application import services
from healthy.application.services import AuthenticatedSession
from healthy.infrastructure.config import Settings
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
    PersonCreate,
    PersonSummary,
    RegistrationResponse,
    SessionCreate,
    SessionSummary,
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
