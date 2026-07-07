"""User management, roles, and per-account access — offline.

Covers: admin CRUD on users, last-admin guards, the member role's visibility
(library/accounts/jobs filtered to the allow-list), member write restrictions,
and store account-access enforcement.

Shares the app DB with the other test modules; cleans up everything it creates.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="unbound-test-"))
os.environ.setdefault("UNBOUND_DATA_DIR", str(_tmp))
os.environ.setdefault("UNBOUND_DATABASE_URL", f"sqlite:///{_tmp / 'test.db'}")
os.environ.setdefault("UNBOUND_SECRET_KEY", "test-master-secret-key-value-1234567890")
os.environ.setdefault("UNBOUND_COOKIE_SECURE", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, col, select  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.models import (  # noqa: E402
    AudibleAccount,
    Book,
    EventLog,
    User,
    UserAccountAccess,
    UserRole,
)
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = {"username": "rbacadmin", "password": "pw-for-rbac-admin"}
MEMBER = {"username": "rbacmember", "password": "pw-for-rbac-member"}


@pytest.fixture()
def env():
    with TestClient(app) as c:
        with Session(engine) as session:
            session.add(User(username=ADMIN["username"], email="rbac@example.com",
                             password_hash=hash_password(ADMIN["password"]), role=UserRole.admin))
            a1 = AudibleAccount(label="RBAC One")
            a2 = AudibleAccount(label="RBAC Two")
            session.add(a1)
            session.add(a2)
            session.commit()
            session.refresh(a1)
            session.refresh(a2)
            session.add(Book(audible_account_id=a1.id, asin="RBAC1", title="Visible Book"))
            session.add(Book(audible_account_id=a2.id, asin="RBAC2", title="Hidden Book"))
            session.commit()
            ids = (a1.id, a2.id)
        c.post("/api/auth/login", json=ADMIN)
        yield c, ids[0], ids[1]
    with Session(engine) as session:
        for u in session.exec(
            select(User).where(col(User.username).like("rbac%"))
        ).all():
            for row in session.exec(
                select(UserAccountAccess).where(UserAccountAccess.user_id == u.id)
            ).all():
                session.delete(row)
            session.delete(u)
        for label in ("RBAC One", "RBAC Two"):
            for a in session.exec(select(AudibleAccount).where(AudibleAccount.label == label)).all():
                for b in session.exec(select(Book).where(Book.audible_account_id == a.id)).all():
                    session.delete(b)
                session.delete(a)
        for e in session.exec(select(EventLog).where(col(EventLog.message).like("%rbac%"))).all():
            session.delete(e)
        session.commit()
    from app.api.routers import auth as auth_router

    auth_router._LOGIN_ATTEMPTS.clear()


def _create_member(c, account_ids: list[int]) -> dict:
    r = c.post("/api/users", json={**MEMBER, "role": "member", "account_ids": account_ids})
    assert r.status_code == 201, r.text
    return r.json()


def _login_member(c) -> None:
    c.post("/api/auth/logout")
    c.cookies.clear()
    assert c.post("/api/auth/login", json=MEMBER).status_code == 200


def test_member_sees_only_allowed_accounts(env):
    c, a1, a2 = env
    created = _create_member(c, [a1])
    assert created["role"] == "member" and created["account_ids"] == [a1]

    _login_member(c)
    accounts = c.get("/api/accounts").json()
    assert [a["id"] for a in accounts] == [a1]

    titles = {b["title"] for b in c.get("/api/library?limit=1000").json()}
    assert "Visible Book" in titles and "Hidden Book" not in titles

    hidden_id = None  # direct fetch of a forbidden book 404s
    # (find it as admin)
    c.post("/api/auth/logout")
    c.cookies.clear()
    c.post("/api/auth/login", json=ADMIN)
    hidden_id = next(
        b["id"] for b in c.get("/api/library?limit=1000").json() if b["title"] == "Hidden Book"
    )
    _login_member(c)
    assert c.get(f"/api/library/{hidden_id}").status_code == 404


def test_member_cannot_mutate_or_admin(env):
    c, a1, _ = env
    _create_member(c, [a1])
    _login_member(c)
    visible = next(
        b for b in c.get("/api/library?limit=1000").json() if b["title"] == "Visible Book"
    )
    assert c.post(f"/api/library/{visible['id']}/exclude", json={"excluded": True}).status_code == 403
    assert c.post(f"/api/library/{visible['id']}/download").status_code == 403
    assert c.post("/api/library/download-all").status_code == 403
    assert c.get("/api/settings/integrations").status_code == 403
    assert c.get("/api/users").status_code == 403
    assert c.post("/api/accounts", json={"label": "nope"}).status_code == 403


def test_member_store_access_enforced(env, monkeypatch):
    c, a1, a2 = env
    _create_member(c, [a1])
    _login_member(c)
    # Allowed account: passes access control (account isn't linked → 400, not 403).
    assert c.get(f"/api/store/search?account_id={a1}&q=x").status_code == 400
    # Forbidden account: blocked before anything else.
    assert c.get(f"/api/store/search?account_id={a2}&q=x").status_code == 403
    assert (
        c.post("/api/store/wishlist", json={"account_id": a2, "asin": "X"}).status_code == 403
    )


def test_last_admin_guards(env):
    c, a1, _ = env
    admins = [u for u in c.get("/api/users").json() if u["role"] == "admin" and u["is_active"]]
    if len(admins) > 1:
        pytest.skip("other admins exist in shared DB")
    me = admins[0]
    assert c.put(f"/api/users/{me['id']}", json={"role": "member"}).status_code == 422
    assert c.put(f"/api/users/{me['id']}", json={"is_active": False}).status_code == 422
    assert c.delete(f"/api/users/{me['id']}").status_code == 422  # also self-delete


def test_update_grants_and_access(env):
    c, a1, a2 = env
    created = _create_member(c, [a1])
    r = c.put(
        f"/api/users/{created['id']}",
        json={"can_spend_credits": True, "account_ids": [a1, a2]},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["can_spend_credits"] is True and sorted(out["account_ids"]) == sorted([a1, a2])

    # /auth/me reflects the grant for the member.
    _login_member(c)
    assert c.get("/api/auth/me").json()["can_spend_credits"] is True
