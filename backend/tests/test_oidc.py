"""OIDC single sign-on tests — fully offline.

Provider discovery, code exchange, and ID-token validation are monkeypatched;
what's exercised is our own flow: authorize-redirect construction (state/PKCE),
callback state validation, claim→user matching, session issuance, and the
admin settings API (secret encrypted at rest, never echoed).

NOTE: test_smoke asserts first-run behavior (``setup_required``), so this module
never uses ``/api/auth/setup`` — it inserts its user directly and removes every
row/setting it created on teardown.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Point config at a throwaway data dir BEFORE importing the app (first importer wins).
_tmp = Path(tempfile.mkdtemp(prefix="unbound-test-"))
os.environ.setdefault("UNBOUND_DATA_DIR", str(_tmp))
os.environ.setdefault("UNBOUND_DATABASE_URL", f"sqlite:///{_tmp / 'test.db'}")
os.environ.setdefault("UNBOUND_SECRET_KEY", "test-master-secret-key-value-1234567890")
os.environ.setdefault("UNBOUND_COOKIE_SECURE", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.api.routers import auth as auth_router  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db import init_db  # noqa: E402
from app.db.models import AppSetting, User, UserRole  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import oidc  # noqa: E402

FAKE_DOC = {
    "authorization_endpoint": "https://idp.example.com/authorize",
    "token_endpoint": "https://idp.example.com/token",
    "jwks_uri": "https://idp.example.com/jwks",
}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(oidc, "discover", lambda issuer: FAKE_DOC)
    with TestClient(app) as c:
        with Session(engine) as session:
            user = User(
                username="oidcuser",
                email="oidc@example.com",
                password_hash=hash_password("pw-for-oidc-tests"),
                role=UserRole.admin,
            )
            session.add(user)
            session.commit()
        yield c
    # Leave the shared DB exactly as found (test_smoke asserts first-run state).
    with Session(engine) as session:
        for u in session.exec(select(User).where(User.username == "oidcuser")).all():
            session.delete(u)
        for key in (
            oidc.SETTING_OIDC_ENABLED,
            oidc.SETTING_OIDC_ISSUER,
            oidc.SETTING_OIDC_CLIENT_ID,
            oidc.SETTING_OIDC_CLIENT_SECRET,
            oidc.SETTING_OIDC_BUTTON_LABEL,
            oidc.SETTING_OIDC_PUBLIC_BASE_URL,
        ):
            row = session.get(AppSetting, key)
            if row:
                session.delete(row)
        session.commit()
    auth_router._LOGIN_ATTEMPTS.clear()
    oidc._flows.clear()


def _configure(enabled: bool = True) -> None:
    with Session(engine) as session:
        init_db.set_setting(session, oidc.SETTING_OIDC_ENABLED, str(enabled).lower())
        init_db.set_setting(session, oidc.SETTING_OIDC_ISSUER, "https://idp.example.com")
        init_db.set_setting(session, oidc.SETTING_OIDC_CLIENT_ID, "unbound-client")
        oidc.set_client_secret(session, "s3cret")


def test_login_404_when_disabled(client):
    assert client.get("/api/auth/oidc/login", follow_redirects=False).status_code == 404
    status = client.get("/api/auth/status").json()
    assert status["oidc_enabled"] is False and status["oidc_button_label"] is None


def test_authorize_redirect_and_status(client):
    _configure()
    status = client.get("/api/auth/status").json()
    assert status["oidc_enabled"] is True
    assert status["oidc_button_label"] == "Single sign-on"

    r = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert r.status_code == 307
    url = urlparse(r.headers["location"])
    q = parse_qs(url.query)
    assert r.headers["location"].startswith(FAKE_DOC["authorization_endpoint"])
    assert q["client_id"] == ["unbound-client"]
    assert q["response_type"] == ["code"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"][0] and q["nonce"][0] and q["code_challenge"][0]
    assert q["redirect_uri"][0].endswith("/api/auth/oidc/callback")


def _start_and_get_state(client) -> str:
    r = client.get("/api/auth/oidc/login", follow_redirects=False)
    return parse_qs(urlparse(r.headers["location"]).query)["state"][0]


def test_callback_signs_in_matching_user(client, monkeypatch):
    _configure()
    state = _start_and_get_state(client)
    flow_nonce = oidc._flows[state]["nonce"]

    def fake_exchange(config, doc, code, verifier, redirect_uri):
        assert code == "authcode" and verifier  # PKCE verifier travels to the token call
        return {"id_token": "fake-id-token"}

    def fake_validate(config, doc, id_token, nonce):
        assert id_token == "fake-id-token" and nonce == flow_nonce
        return {"sub": "abc", "email": "OIDC@example.com"}  # case-insensitive email match

    monkeypatch.setattr(oidc, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc, "_validate_id_token", fake_validate)

    r = client.get(
        f"/api/auth/oidc/callback?code=authcode&state={state}", follow_redirects=False
    )
    assert r.status_code == 307 and r.headers["location"] == "/"
    assert client.get("/api/auth/me").json()["username"] == "oidcuser"
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_callback_rejects_bad_state_and_unmatched_user(client, monkeypatch):
    _configure()

    # Unknown state → back to login with an error, no session.
    r = client.get("/api/auth/oidc/callback?code=x&state=bogus", follow_redirects=False)
    assert r.status_code == 307 and "oidc_error=" in r.headers["location"]
    assert client.get("/api/auth/me").status_code == 401

    # Verified identity that matches no local user → rejected.
    state = _start_and_get_state(client)
    monkeypatch.setattr(oidc, "_exchange_code", lambda *a, **k: {"id_token": "t"})
    monkeypatch.setattr(
        oidc, "_validate_id_token", lambda *a, **k: {"sub": "x", "email": "stranger@example.com"}
    )
    r = client.get(f"/api/auth/oidc/callback?code=c&state={state}", follow_redirects=False)
    assert "oidc_error=" in r.headers["location"]
    assert client.get("/api/auth/me").status_code == 401
    # A state is single-use: replaying the same one must fail even with valid claims.
    r = client.get(f"/api/auth/oidc/callback?code=c&state={state}", follow_redirects=False)
    assert "oidc_error=" in r.headers["location"]


def test_settings_api_encrypts_and_never_echoes_secret(client):
    # The settings API needs an authenticated admin session.
    r = client.post(
        "/api/auth/login", json={"username": "oidcuser", "password": "pw-for-oidc-tests"}
    )
    assert r.status_code == 200, r.text

    r = client.put(
        "/api/settings/oidc",
        json={
            "enabled": True,
            "issuer": "https://idp.example.com/",
            "client_id": "unbound-client",
            "client_secret": "super-secret-value",
            "button_label": "Sign in with Authentik",
            "public_base_url": "https://unbound.example.com/",
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["client_secret"] is None and out["client_secret_set"] is True
    assert out["issuer"] == "https://idp.example.com"  # trailing slash normalized
    assert out["redirect_uri"] == "https://unbound.example.com/api/auth/oidc/callback"

    # Stored encrypted, not plaintext.
    with Session(engine) as session:
        raw = init_db.get_setting(session, oidc.SETTING_OIDC_CLIENT_SECRET)
        assert raw and "super-secret-value" not in raw
        config = oidc.get_config(session)
        assert config and config.client_secret == "super-secret-value"
        assert config.button_label == "Sign in with Authentik"

    # Updating without sending a secret keeps the stored one.
    r = client.put(
        "/api/settings/oidc",
        json={"enabled": True, "issuer": "https://idp.example.com", "client_id": "unbound-client"},
    )
    assert r.status_code == 200 and r.json()["client_secret_set"] is True

    # Enabling without the required fields is rejected.
    r = client.put("/api/settings/oidc", json={"enabled": True, "issuer": "", "client_id": ""})
    assert r.status_code == 422
    client.post("/api/auth/logout")
    client.cookies.clear()
