"""Store (Audible search + wishlist) router tests — offline (audible client mocked).

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
from app.db.models import (  # noqa: E402
    AccountStatus,
    AudibleAccount,
    Book,
    EventLog,
    User,
    UserRole,
)
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import accounts as account_svc  # noqa: E402


def _item(asin: str, title: str) -> ac.StoreItem:
    return ac.StoreItem(
        asin=asin, title=title, subtitle=None, authors="Some Author", narrators="Some Narrator",
        series=None, series_sequence=None, runtime_minutes=321, cover_url=None,
        price_display="14.95 USD", release_date="2024-01-01", raw={},
    )


@pytest.fixture()
def client(monkeypatch):
    wishlist_state: list[ac.StoreItem] = [_item("WISH1", "Wished For")]

    monkeypatch.setattr(account_svc, "load_authenticator", lambda account: object())
    monkeypatch.setattr(
        ac, "search_catalog",
        lambda auth, q, page=0, num_results=20: ([_item("OWNED1", "Already Owned"), _item("NEW1", "Shiny New")], 2),
    )
    monkeypatch.setattr(ac, "get_wishlist", lambda auth: list(wishlist_state))
    monkeypatch.setattr(
        ac, "add_to_wishlist", lambda auth, asin: wishlist_state.append(_item(asin, asin))
    )
    monkeypatch.setattr(
        ac, "remove_from_wishlist",
        lambda auth, asin: wishlist_state[:] and wishlist_state.remove(
            next(i for i in wishlist_state if i.asin == asin)
        ),
    )

    with TestClient(app) as c:
        with Session(engine) as session:
            session.add(
                User(username="storeuser", email="store@example.com",
                     password_hash=hash_password("pw-for-store-tests"), role=UserRole.admin)
            )
            linked = AudibleAccount(label="Store Test", status=AccountStatus.linked,
                                    encrypted_auth_blob="ignored-by-mock")
            unlinked = AudibleAccount(label="Store Unlinked")
            session.add(linked)
            session.add(unlinked)
            session.commit()
            session.refresh(linked)
            session.refresh(unlinked)
            session.add(Book(audible_account_id=linked.id, asin="OWNED1", title="Already Owned"))
            session.commit()
            ids = (linked.id, unlinked.id)
        c.post("/api/auth/login", json={"username": "storeuser", "password": "pw-for-store-tests"})
        yield c, ids[0], ids[1]
    with Session(engine) as session:
        for label in ("Store Test", "Store Unlinked"):
            for account in session.exec(
                select(AudibleAccount).where(AudibleAccount.label == label)
            ).all():
                for book in session.exec(
                    select(Book).where(Book.audible_account_id == account.id)
                ).all():
                    session.delete(book)
                session.delete(account)
        for u in session.exec(select(User).where(User.username == "storeuser")).all():
            session.delete(u)
        for e in session.exec(
            select(EventLog).where(col(EventLog.message).like("%ishlist%"))
        ).all():
            session.delete(e)
        session.commit()
    from app.api.routers import auth as auth_router

    auth_router._LOGIN_ATTEMPTS.clear()


def test_search_flags_owned_titles(client):
    c, account_id, _ = client
    r = c.get(f"/api/store/search?account_id={account_id}&q=test")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["total"] == 2
    by_asin = {i["asin"]: i for i in out["items"]}
    assert by_asin["OWNED1"]["in_library"] is True
    assert by_asin["NEW1"]["in_library"] is False
    assert by_asin["NEW1"]["price_display"] == "14.95 USD"


def test_search_requires_linked_account(client):
    c, _, unlinked_id = client
    assert c.get(f"/api/store/search?account_id={unlinked_id}&q=test").status_code == 400
    assert c.get("/api/store/search?account_id=999999&q=test").status_code == 404


def test_wishlist_roundtrip(client):
    c, account_id, _ = client
    r = c.get(f"/api/store/wishlist?account_id={account_id}")
    assert r.status_code == 200 and [i["asin"] for i in r.json()] == ["WISH1"]

    assert (
        c.post("/api/store/wishlist", json={"account_id": account_id, "asin": "NEW1"}).status_code
        == 201
    )
    r = c.get(f"/api/store/wishlist?account_id={account_id}")
    assert {i["asin"] for i in r.json()} == {"WISH1", "NEW1"}

    assert c.delete(f"/api/store/wishlist/NEW1?account_id={account_id}").status_code == 204
    r = c.get(f"/api/store/wishlist?account_id={account_id}")
    assert [i["asin"] for i in r.json()] == ["WISH1"]


def test_store_requires_auth(client):
    c, account_id, _ = client
    c.post("/api/auth/logout")
    c.cookies.clear()
    assert c.get(f"/api/store/search?account_id={account_id}&q=x").status_code == 401
