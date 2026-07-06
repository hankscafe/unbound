"""Shared API dependencies: DB session, current-user auth, API-key auth."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.core.config import get_settings
from app.core.security import decode_session_token
from app.db.models import ApiKey, User
from app.db.session import get_session

settings = get_settings()


def db_session() -> Session:
    yield from get_session()


def _read_session_cookie(request: Request) -> str | None:
    return request.cookies.get(settings.cookie_name)


def current_user(
    request: Request, session: Session = Depends(db_session)
) -> User:
    token = _read_session_cookie(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_session_token(token, session_secret=settings.resolve_session_secret())
    except Exception as exc:  # invalid/expired
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session") from exc
    user = session.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin privileges required")
    return user


def _hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def api_key_or_session(
    request: Request, session: Session = Depends(db_session)
) -> User | ApiKey:
    """Allow either a logged-in session OR a valid read API key.

    Used by widget-friendly endpoints (``/stats``, ``/health``) so Homepage can poll
    without a browser session.
    """
    header_key = request.headers.get("X-API-Key")
    if header_key:
        row = session.exec(
            select(ApiKey).where(
                ApiKey.key_hash == _hash_api_key(header_key), ApiKey.is_active == True  # noqa: E712
            )
        ).first()
        if row:
            row.last_used_at = datetime.now(timezone.utc)
            session.add(row)
            session.commit()
            return row
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    return current_user(request, session)
