"""End-to-end smoke test of the core web flow (no Audible/ffmpeg needed).

Runs the real FastAPI app against a temp SQLite DB: first-run setup, login,
auth-gated endpoints, stats, and library-profile CRUD. Also validates the
SecretBox encrypt/decrypt roundtrip and log redaction.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Point config at a throwaway data dir BEFORE importing the app.
_tmp = Path(tempfile.mkdtemp(prefix="unbound-test-"))
os.environ["UNBOUND_DATA_DIR"] = str(_tmp)
os.environ["UNBOUND_DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["UNBOUND_SECRET_KEY"] = "test-master-secret-key-value-1234567890"
os.environ["UNBOUND_COOKIE_SECURE"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.logging import redact_processor  # noqa: E402
from app.core.security import SecretBox  # noqa: E402
from app.main import app  # noqa: E402


def test_secretbox_roundtrip():
    box = SecretBox("some-master-secret")
    token = box.encrypt("device-private-key-material")
    assert token != "device-private-key-material"
    assert box.decrypt_str(token) == "device-private-key-material"
    # Wrong key cannot decrypt.
    try:
        SecretBox("different-secret").decrypt(token)
        assert False, "should not decrypt with wrong key"
    except Exception:
        pass


def test_log_redaction():
    out = redact_processor(None, "info", {"event": "login", "password": "hunter2", "otp": "123456"})
    assert out["password"] == "***REDACTED***"
    assert out["otp"] == "***REDACTED***"
    assert out["event"] == "login"


def _exercise_2fa(client):
    """Enroll TOTP, log in via 2FA and a recovery code, then disable it."""
    import pyotp

    r = client.post("/api/auth/2fa/setup")
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["qr"].startswith("data:image/png;base64,")

    # Enabling requires a valid current code.
    assert client.post("/api/auth/2fa/enable", json={"code": "000000"}).status_code == 400
    r = client.post("/api/auth/2fa/enable", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    recovery = r.json()["recovery_codes"]
    assert len(recovery) == 10
    assert client.get("/api/auth/me").json()["totp_enabled"] is True

    # Login now demands a second factor.
    client.post("/api/auth/logout")
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": "admin", "password": "supersecret1"})
    assert r.json()["mfa_required"] is True and r.json()["user"] is None
    token = r.json()["mfa_token"]
    # Wrong code rejected; correct TOTP accepted.
    assert client.post("/api/auth/login/2fa", json={"mfa_token": token, "code": "000000"}).status_code == 401
    r = client.post("/api/auth/login/2fa", json={"mfa_token": token, "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200 and r.json()["user"]["username"] == "admin"

    # A recovery code also works (one-time).
    client.post("/api/auth/logout")
    client.cookies.clear()
    token = client.post("/api/auth/login", json={"username": "admin", "password": "supersecret1"}).json()["mfa_token"]
    assert client.post("/api/auth/login/2fa", json={"mfa_token": token, "code": recovery[0]}).status_code == 200
    # The same recovery code cannot be reused.
    client.post("/api/auth/logout")
    client.cookies.clear()
    token = client.post("/api/auth/login", json={"username": "admin", "password": "supersecret1"}).json()["mfa_token"]
    assert client.post("/api/auth/login/2fa", json={"mfa_token": token, "code": recovery[0]}).status_code == 401
    token = client.post("/api/auth/login", json={"username": "admin", "password": "supersecret1"}).json()["mfa_token"]
    client.post("/api/auth/login/2fa", json={"mfa_token": token, "code": pyotp.TOTP(secret).now()})

    # Disable restores single-factor login.
    assert client.post("/api/auth/2fa/disable", json={"code": pyotp.TOTP(secret).now()}).status_code == 204
    assert client.get("/api/auth/me").json()["totp_enabled"] is False
    r = client.post("/api/auth/logout")
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": "admin", "password": "supersecret1"})
    assert r.json()["mfa_required"] is False and r.json()["user"]["username"] == "admin"


def test_full_flow():
    with TestClient(app) as client:
        # Fresh install → setup required.
        r = client.get("/api/auth/status")
        assert r.status_code == 200, r.text
        assert r.json()["setup_required"] is True

        # Health + login covers are open (unauthenticated).
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/covers").json()["covers"] == []  # no books yet

        # Cannot list accounts before auth.
        assert client.get("/api/accounts").status_code == 401

        # Setup requires consent.
        assert client.post("/api/auth/setup", json={
            "username": "admin", "password": "supersecret1", "consent": False,
        }).status_code == 400

        # Complete setup → logged in via cookie.
        r = client.post("/api/auth/setup", json={
            "username": "admin", "password": "supersecret1", "email": "a@b.c", "consent": True,
        })
        assert r.status_code == 201, r.text
        assert r.json()["role"] == "admin"

        # Now authenticated.
        assert client.get("/api/auth/status").json()["setup_required"] is False
        assert client.get("/api/auth/me").json()["username"] == "admin"

        _exercise_2fa(client)

        # Stats reflect an empty library.
        stats = client.get("/api/stats").json()
        assert stats["accounts_total"] == 0
        assert stats["books_total"] == 0
        assert "current_version" in stats

        # Create an Audible account shell (unlinked).
        r = client.post("/api/accounts", json={"label": "Main", "marketplace": "us"})
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "unlinked"

        # Library profile CRUD.
        r = client.post("/api/settings/library-profiles", json={
            "name": "Default", "root_path": str(_tmp / "library"),
            "folder_template": "{author}/{series}", "filename_template": "{title}",
            "is_default": True,
        })
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        assert client.get("/api/settings/library-profiles").json()[0]["id"] == pid

        # Create + use a read API key for stats (no session).
        key = client.post("/api/settings/api-keys", json={"name": "homepage"}).json()["key"]
        assert key.startswith("unb_")

        # Logout clears the session.
        assert client.post("/api/auth/logout").status_code == 204
        client.cookies.clear()
        assert client.get("/api/accounts").status_code == 401

        # API key still authorizes stats.
        r = client.get("/api/stats", headers={"X-API-Key": key})
        assert r.status_code == 200, r.text
        assert r.json()["accounts_total"] == 1
