"""Credit-purchase endpoint tests — offline (order call mocked).

Every gate is exercised: global switch, per-user grant, account access,
already-owned guard; success must log, notify, and queue the follow-up task.

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

from app.audible import client as ac  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db import init_db  # noqa: E402
from app.db.models import (  # noqa: E402
    AccountStatus,
    AppSetting,
    AudibleAccount,
    Book,
    EventLog,
    User,
    UserRole,
)
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import accounts as account_svc  # noqa: E402
from app.services import notifier  # noqa: E402
from app.worker import queue as queue_mod  # noqa: E402

BUYER = {"username": "buyeruser", "password": "pw-for-buyer-tests"}


@pytest.fixture()
def env(monkeypatch):
    calls: dict = {"orders": [], "enqueued": [], "notified": []}

    monkeypatch.setattr(account_svc, "load_authenticator", lambda account: object())
    monkeypatch.setattr(
        ac, "purchase_with_credit",
        lambda auth, asin: calls["orders"].append(asin) or {"order_id": f"ORD-{asin}"},
    )
    monkeypatch.setattr(
        queue_mod, "enqueue", lambda name, **kw: calls["enqueued"].append((name, kw))
    )
    monkeypatch.setattr(
        notifier, "send_if_enabled",
        lambda key, title, body, default=False: calls["notified"].append(title),
    )

    with TestClient(app) as c:
        with Session(engine) as session:
            session.add(User(username=BUYER["username"], email="buyer@example.com",
                             password_hash=hash_password(BUYER["password"]),
                             role=UserRole.admin, can_spend_credits=True))
            account = AudibleAccount(label="Buy Test", status=AccountStatus.linked,
                                     encrypted_auth_blob="ignored-by-mock")
            session.add(account)
            session.commit()
            session.refresh(account)
            session.add(Book(audible_account_id=account.id, asin="OWNEDBUY", title="Owned"))
            init_db.set_setting(session, init_db.SETTING_PURCHASES_ENABLED, "true")
            session.commit()
            aid = account.id
        c.post("/api/auth/login", json=BUYER)
        yield c, aid, calls
    with Session(engine) as session:
        for a in session.exec(select(AudibleAccount).where(AudibleAccount.label == "Buy Test")).all():
            for b in session.exec(select(Book).where(Book.audible_account_id == a.id)).all():
                session.delete(b)
            session.delete(a)
        for u in session.exec(select(User).where(User.username == BUYER["username"])).all():
            session.delete(u)
        row = session.get(AppSetting, init_db.SETTING_PURCHASES_ENABLED)
        if row:
            session.delete(row)
        for e in session.exec(select(EventLog).where(col(EventLog.category) == "store")).all():
            session.delete(e)
        session.commit()
    from app.api.routers import auth as auth_router

    auth_router._LOGIN_ATTEMPTS.clear()


def _buy(c, aid, asin="NEWBUY1", title="A New Book"):
    return c.post("/api/store/purchase", json={"account_id": aid, "asin": asin, "title": title})


def test_purchase_success_logs_notifies_and_queues(env):
    c, aid, calls = env
    r = _buy(c, aid)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["ok"] is True and out["order_id"] == "ORD-NEWBUY1"
    assert calls["orders"] == ["NEWBUY1"]
    assert calls["enqueued"] == [("post_purchase", {"account_id": aid, "asin": "NEWBUY1"})]
    assert calls["notified"] and "purchased" in calls["notified"][0].lower()
    with Session(engine) as session:
        events = session.exec(select(EventLog).where(col(EventLog.category) == "store")).all()
        assert any("buyeruser" in e.message and "NEWBUY1" in e.message for e in events)


def test_purchase_blocked_when_globally_disabled(env):
    c, aid, calls = env
    with Session(engine) as session:
        init_db.set_setting(session, init_db.SETTING_PURCHASES_ENABLED, "false")
    r = _buy(c, aid)
    assert r.status_code == 403 and "disabled" in r.json()["detail"].lower()
    assert calls["orders"] == []


def test_purchase_requires_spend_grant(env):
    c, aid, calls = env
    with Session(engine) as session:
        u = session.exec(select(User).where(User.username == BUYER["username"])).one()
        u.can_spend_credits = False
        session.add(u)
        session.commit()
    r = _buy(c, aid)
    assert r.status_code == 403 and "permission" in r.json()["detail"].lower()
    assert calls["orders"] == []


def test_purchase_rejects_already_owned(env):
    c, aid, calls = env
    r = _buy(c, aid, asin="OWNEDBUY", title="Owned")
    assert r.status_code == 409
    assert calls["orders"] == []


def test_member_needs_account_access_to_buy(env):
    c, aid, calls = env
    with Session(engine) as session:
        session.add(User(username="buyermember", email=None,
                         password_hash=hash_password("pw-for-buyer-member"),
                         role=UserRole.member, can_spend_credits=True))
        session.commit()
    c.post("/api/auth/logout")
    c.cookies.clear()
    c.post("/api/auth/login", json={"username": "buyermember", "password": "pw-for-buyer-member"})
    r = _buy(c, aid)  # no account access rows → forbidden
    assert r.status_code == 403
    assert calls["orders"] == []
    with Session(engine) as session:
        for u in session.exec(select(User).where(User.username == "buyermember")).all():
            session.delete(u)
        session.commit()
