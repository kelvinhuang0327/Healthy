from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, cast

from fastapi import Cookie, Depends, Header, HTTPException, Request, Security, status
from fastapi.security import APIKeyCookie
from sqlalchemy.orm import Session

from healthy.application.services import AuthenticatedSession, resolve_session
from healthy.infrastructure.config import Settings
from healthy.infrastructure.database import Database
from healthy.infrastructure.security import validate_csrf_token

SESSION_COOKIE = "healthy_session"
CSRF_COOKIE = "healthy_csrf"
CSRF_HEADER = "X-CSRF-Token"

session_cookie_scheme = APIKeyCookie(
    name=SESSION_COOKIE,
    scheme_name="CookieSession",
    description="Opaque server-managed session cookie.",
    auto_error=False,
)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_database_session(request: Request) -> Iterator[Session]:
    database: Database = request.app.state.database
    yield from database.sessions()


def require_origin(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    origin = request.headers.get("origin")
    if origin is None or origin.rstrip("/") not in settings.allowed_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin rejected",
        )
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-site command rejected",
        )


def get_authenticated_session(
    raw_token: Annotated[str | None, Security(session_cookie_scheme)],
    database_session: Annotated[Session, Depends(get_database_session)],
) -> AuthenticatedSession:
    if raw_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    authenticated = resolve_session(database_session, raw_token=raw_token)
    if authenticated is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return authenticated


def get_command_session(
    request: Request,
    authenticated: Annotated[AuthenticatedSession, Depends(get_authenticated_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
) -> AuthenticatedSession:
    require_origin(request, settings)
    if (
        csrf_cookie is None
        or csrf_header is None
        or not validate_csrf_token(
            session_id=authenticated.session.id,
            cookie_token=csrf_cookie,
            header_token=csrf_header,
            secret=settings.csrf_secret,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )
    return authenticated
