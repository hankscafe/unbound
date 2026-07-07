"""Sliding idle-timeout session tests.

Login issues a cookie that expires after ``idle_timeout_seconds``; /auth/refresh
slides that window while preserving ``auth_time`` so the absolute
``session_ttl_seconds`` lifetime still caps it.

Shares the app DB with the other test modules (test_smoke asserts first-run
state), so everything created here is removed on teardown.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="unbound-test-"))
os.environ.setdefault("UNBOUND_DATA_DIR", str(_tmp))
os.environ.setdefault("UNBOUND_DATABASE_URL", f"sqlite:///{_tmp / 'test.db'}")
os.environ.setdefault("UNBOUND_SECRET_KEY", "test-master-secret-key-value-1234567890")
os.environ.setdefault("UNBOUND_COOKIE_SECURE", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.api.routers import auth as auth_router  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.security import (  # noqa: E402
    create_session_token,
    decode_session_token,
    hash_password,
)
from app.db.models import User, UserRole  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402

settings = get_settings()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        with Session(engine) as session:
            user = User(
                username="sessionuser",
                email="session@example.com",
                password_hash=hash_password("pw-for-session-tests"),
                role=UserRole.admin,
            )
            session.add(user)
            session.commit()
        yield c
    with Session(engine) as session:
        for u in session.exec(select(User).where(User.username == "sessionuser")).all():
            session.delete(u)
        session.commit()
    auth_router._LOGIN_ATTEMPTS.clear()


def _login(client) -> str:
    r = client.post(
        "/api/auth/login", json={"username": "sessionuser", "password": "pw-for-session-tests"}
    )
    assert r.status_code == 200, r.text
    return client.cookies.get(settings.cookie_name)


def test_login_cookie_uses_idle_ttl(client):
    token = _login(client)
    claims = decode_session_token(token, session_secret=settings.resolve_session_secret())
    assert claims["exp"] - claims["iat"] == settings.idle_timeout_seconds
    assert claims["auth_time"] == claims["iat"]
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_refresh_slides_window_and_preserves_auth_time(client):
    first = _login(client)
    c1 = decode_session_token(first, session_secret=settings.resolve_session_secret())
    time.sleep(1.1)  # ensure a later exp on the refreshed token
    r = client.post("/api/auth/refresh")
    assert r.status_code == 204
    second = client.cookies.get(settings.cookie_name)
    c2 = decode_session_token(second, session_secret=settings.resolve_session_secret())
    assert c2["exp"] > c1["exp"]  # window slid forward
    assert c2["auth_time"] == c1["auth_time"]  # original login time carried over
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_idle_expired_session_is_rejected(client):
    with Session(engine) as session:
        uid = session.exec(select(User).where(User.username == "sessionuser")).one().id
    expired = create_session_token(
        subject=str(uid),
        session_secret=settings.resolve_session_secret(),
        ttl_seconds=-10,  # already past the idle deadline
    )
    client.cookies.set(settings.cookie_name, expired)
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/refresh").status_code == 401
    client.cookies.clear()


def test_absolute_lifetime_caps_refresh(client):
    with Session(engine) as session:
        uid = session.exec(select(User).where(User.username == "sessionuser")).one().id
    # Token still inside its idle window, but the original login is older than
    # the absolute session lifetime → refresh must refuse to keep sliding.
    stale_auth = int(time.time()) - settings.session_ttl_seconds - 60
    token = create_session_token(
        subject=str(uid),
        session_secret=settings.resolve_session_secret(),
        ttl_seconds=settings.idle_timeout_seconds,
        auth_time=stale_auth,
    )
    client.cookies.set(settings.cookie_name, token)
    assert client.get("/api/auth/me").status_code == 200  # idle window still valid
    assert client.post("/api/auth/refresh").status_code == 401  # but no more sliding
    client.cookies.clear()


def test_refresh_requires_auth(client):
    assert client.post("/api/auth/refresh").status_code == 401
