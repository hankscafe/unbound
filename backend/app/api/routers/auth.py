"""Authentication & first-run setup endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session, select

from app.api.deps import current_user, db_session
from app.core.config import get_settings
from app.core.security import (
    create_session_token,
    needs_rehash,
    hash_password,
    verify_password,
)
from app.db import init_db
from app.db.models import EventLog, User, UserRole
from app.schemas import LoginRequest, SetupRequest, StatusOut, UserOut

router = APIRouter(tags=["auth"])
settings = get_settings()

# --- Simple in-memory login rate limiting (per client IP) ---
import time as _time
from collections import defaultdict

_LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_MAX_ATTEMPTS = 8
_WINDOW_SECONDS = 300  # 5 minutes


def _rate_limit_login(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = _time.time()
    recent = [t for t in _LOGIN_ATTEMPTS[ip] if now - t < _WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[ip] = recent
    if len(recent) >= _MAX_ATTEMPTS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many login attempts. Try again in a few minutes.",
        )


def _record_login_attempt(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    _LOGIN_ATTEMPTS[ip].append(_time.time())


def _set_session_cookie(response: Response, user_id: int) -> None:
    token = create_session_token(
        subject=str(user_id),
        session_secret=settings.resolve_session_secret(),
        ttl_seconds=settings.session_ttl_seconds,
    )
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


@router.get("/status", response_model=StatusOut)
def get_status(request: Request, session: Session = Depends(db_session)) -> StatusOut:
    from app import __version__

    authed = False
    token = request.cookies.get(settings.cookie_name)
    if token:
        try:
            from app.core.security import decode_session_token

            decode_session_token(token, session_secret=settings.resolve_session_secret())
            authed = True
        except Exception:
            authed = False
    return StatusOut(
        setup_required=init_db.setup_required(session),
        authenticated=authed,
        consent_acknowledged=init_db.get_setting(session, init_db.SETTING_CONSENT) == "true",
        secret_key_rotated=init_db.secret_key_rotated(session),
        version=__version__,
    )


@router.post("/setup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def setup(
    payload: SetupRequest, response: Response, session: Session = Depends(db_session)
) -> UserOut:
    if not init_db.setup_required(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup already completed")
    if not payload.consent:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "You must acknowledge the personal-use terms to continue",
        )
    user = init_db.create_admin(
        session, username=payload.username, password=payload.password, email=payload.email
    )
    init_db.set_setting(session, init_db.SETTING_CONSENT, "true")
    init_db.seed_defaults(session)
    session.add(EventLog(category="auth", message=f"Admin account '{user.username}' created"))
    session.commit()
    _set_session_cookie(response, user.id)  # type: ignore[arg-type]
    return UserOut(id=user.id, username=user.username, email=user.email, role=user.role.value)  # type: ignore[arg-type]


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(db_session),
) -> UserOut:
    _rate_limit_login(request)
    user = session.exec(select(User).where(User.username == payload.username)).first()
    # Constant-ish work whether or not the user exists.
    if user is None or not verify_password(payload.password, user.password_hash):
        _record_login_attempt(request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    user.last_login_at = datetime.now(timezone.utc)
    session.add(user)
    session.add(EventLog(category="auth", message=f"User '{user.username}' logged in"))
    session.commit()
    _set_session_cookie(response, user.id)  # type: ignore[arg-type]
    return UserOut(id=user.id, username=user.username, email=user.email, role=user.role.value)  # type: ignore[arg-type]


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    response.delete_cookie(settings.cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut(id=user.id, username=user.username, email=user.email, role=user.role.value)  # type: ignore[arg-type]
