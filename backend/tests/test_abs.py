"""AudiobookShelf already-in-library detection — offline (item resolution mocked).

Covers: match_all now matches every book; the abs_auto_exclude setting excludes
matched-but-not-downloaded titles; an admin re-include is sticky (the book is
never auto-excluded again); our own completed downloads are never auto-excluded.

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
from app.db import init_db  # noqa: E402
from app.db.models import (  # noqa: E402
    AccountStatus,
    AppSetting,
    AudibleAccount,
    Book,
    DownloadJob,
    EventLog,
    JobState,
    User,
    UserRole,
)
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import audiobookshelf as abs_svc  # noqa: E402

# ABS pretends to have these ASINs already.
IN_ABS = {"ASINABS1", "ASINABS2"}


def _resolve_mock(base, token, book, library_id=None):
    return f"item-{book.asin}" if book.asin in IN_ABS else None


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(abs_svc, "resolve_item_id", _resolve_mock)
    # All matched items exist in ABS (one library) unless a test says otherwise.
    monkeypatch.setattr(
        abs_svc,
        "fetch_item_ids_by_library",
        lambda base, token: {"lib-main": {f"item-{a}" for a in IN_ABS}},
    )
    with TestClient(app) as c:
        with Session(engine) as session:
            session.add(
                User(username="absuser", email="abs@example.com",
                     password_hash=hash_password("pw-for-abs-tests"), role=UserRole.admin)
            )
            account = AudibleAccount(label="ABS Test", status=AccountStatus.linked)
            session.add(account)
            session.commit()
            session.refresh(account)
            books = [
                Book(audible_account_id=account.id, asin="ASINABS1", title="Already There"),
                Book(audible_account_id=account.id, asin="ASINABS2", title="Downloaded By Us"),
                Book(audible_account_id=account.id, asin="ASINNEW1", title="Not In ABS"),
            ]
            session.add_all(books)
            session.commit()
            # "Downloaded By Us" was completed by unbound itself.
            done = session.exec(select(Book).where(Book.asin == "ASINABS2")).one()
            session.add(DownloadJob(book_id=done.id, state=JobState.completed))
            # ABS connection configured.
            init_db.set_setting(session, init_db.SETTING_ABS_URL, "https://abs.example.com")
            abs_svc.set_token(session, "abs-test-token")
            init_db.set_setting(session, init_db.SETTING_ABS_AUTO_EXCLUDE, "true")
            session.commit()
        c.post("/api/auth/login", json={"username": "absuser", "password": "pw-for-abs-tests"})
        yield c
    with Session(engine) as session:
        for account in session.exec(select(AudibleAccount).where(AudibleAccount.label == "ABS Test")).all():
            for book in session.exec(select(Book).where(Book.audible_account_id == account.id)).all():
                for job in session.exec(select(DownloadJob).where(DownloadJob.book_id == book.id)).all():
                    session.delete(job)
                session.delete(book)
            session.delete(account)
        for u in session.exec(select(User).where(User.username == "absuser")).all():
            session.delete(u)
        for key in (init_db.SETTING_ABS_URL, init_db.SETTING_ABS_TOKEN, init_db.SETTING_ABS_AUTO_EXCLUDE):
            row = session.get(AppSetting, key)
            if row:
                session.delete(row)
        for e in session.exec(select(EventLog).where(col(EventLog.category) == "library")).all():
            session.delete(e)
        session.commit()
    from app.api.routers import auth as auth_router

    auth_router._LOGIN_ATTEMPTS.clear()


def _book(client, asin: str) -> dict:
    books = client.get("/api/library?limit=1000").json()
    return next(b for b in books if b["asin"] == asin)


def test_match_all_badges_and_auto_excludes(client):
    r = client.post("/api/library/abs-match")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["matched"] == 2 and out["auto_excluded"] == 1

    already = _book(client, "ASINABS1")  # in ABS, not downloaded by us → skipped
    assert already["abs_present"] is True
    assert already["excluded"] is True and already["abs_auto_excluded"] is True
    assert already["abs_url"]  # deep link even without a completed download

    ours = _book(client, "ASINABS2")  # our own completed download → untouched
    assert ours["abs_present"] is True and ours["excluded"] is False

    fresh = _book(client, "ASINNEW1")  # not in ABS → untouched
    assert fresh["abs_present"] is False and fresh["excluded"] is False


def test_reinclude_is_sticky(client):
    client.post("/api/library/abs-match")
    already = _book(client, "ASINABS1")

    # Admin re-includes the auto-excluded book…
    r = client.post(f"/api/library/{already['id']}/exclude", json={"excluded": False})
    assert r.status_code == 200
    assert r.json()["excluded"] is False and r.json()["abs_auto_excluded"] is False

    # …and a re-match must NOT exclude it again.
    client.post("/api/library/abs-match?rematch=true")
    again = _book(client, "ASINABS1")
    assert again["excluded"] is False
    assert again["abs_present"] is True  # still badged as present in ABS


def test_auto_exclude_respects_setting_off(client):
    with Session(engine) as session:
        init_db.set_setting(session, init_db.SETTING_ABS_AUTO_EXCLUDE, "false")
    client.post("/api/library/abs-match")
    already = _book(client, "ASINABS1")
    assert already["abs_present"] is True  # badge yes
    assert already["excluded"] is False  # exclusion no


def test_batch_routes_not_shadowed_by_book_id(client):
    # Regression: /batch/* must not be captured by the /{book_id}/* routes.
    b = _book(client, "ASINNEW1")
    r = client.post("/api/library/batch/exclude", json={"book_ids": [b["id"]], "excluded": True})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1
    client.post("/api/library/batch/exclude", json={"book_ids": [b["id"]], "excluded": False})


def test_vanished_item_flags_our_download_missing(client, monkeypatch):
    client.post("/api/library/abs-match")
    # ASINABS2 (our completed download) disappears from ABS; nothing re-matches.
    monkeypatch.setattr(
        abs_svc, "fetch_item_ids_by_library", lambda base, token: {"lib-main": {"item-ASINABS1"}}
    )
    monkeypatch.setattr(abs_svc, "resolve_item_id", lambda *a, **k: None)
    r = client.post("/api/library/abs-match")
    assert r.status_code == 200
    assert r.json()["missing"] == 1

    ours = _book(client, "ASINABS2")
    assert ours["abs_missing"] is True
    assert ours["abs_present"] is False  # stale deep link dropped
    # Admins get a warning event naming the title.
    with Session(engine) as session:
        ev = session.exec(
            select(EventLog).where(col(EventLog.message).contains("Missing from AudiobookShelf"))
        ).first()
        assert ev is not None and ev.level == "warning"

    # Despite its completed job, the missing book can be re-queued in bulk.
    import app.api.routers.library as library_router

    queued: list[dict] = []
    monkeypatch.setattr(library_router, "enqueue", lambda name, **kw: queued.append(kw))
    r = client.post("/api/library/batch/download", json={"book_ids": [ours["id"]]})
    assert r.status_code == 202, r.text
    assert r.json()["queued"] == 1 and len(queued) == 1


def test_vanished_item_lifts_auto_exclusion(client, monkeypatch):
    client.post("/api/library/abs-match")  # auto-excludes ASINABS1 (not ours)
    monkeypatch.setattr(
        abs_svc, "fetch_item_ids_by_library", lambda base, token: {"lib-main": {"item-ASINABS2"}}
    )
    monkeypatch.setattr(abs_svc, "resolve_item_id", lambda *a, **k: None)
    client.post("/api/library/abs-match")
    b = _book(client, "ASINABS1")
    assert b["excluded"] is False and b["abs_auto_excluded"] is False
    assert b["abs_missing"] is False  # we never downloaded it, so it is not "missing"


def test_empty_abs_item_list_is_not_a_mass_removal(client, monkeypatch):
    client.post("/api/library/abs-match")
    monkeypatch.setattr(
        abs_svc, "fetch_item_ids_by_library", lambda base, token: {"lib-main": set()}
    )
    client.post("/api/library/abs-match")
    ours = _book(client, "ASINABS2")
    assert ours["abs_missing"] is False and ours["abs_present"] is True


def test_patch_account_abs_library_id(client):
    with Session(engine) as session:
        account = session.exec(
            select(AudibleAccount).where(AudibleAccount.label == "ABS Test")
        ).one()
        account_id = account.id
    r = client.patch(f"/api/accounts/{account_id}", json={"abs_library_id": "lib-main"})
    assert r.status_code == 200, r.text
    assert r.json()["abs_library_id"] == "lib-main"
    # Echoed back by the listing; an empty string clears it.
    listed = next(a for a in client.get("/api/accounts").json() if a["id"] == account_id)
    assert listed["abs_library_id"] == "lib-main"
    r = client.patch(f"/api/accounts/{account_id}", json={"abs_library_id": ""})
    assert r.status_code == 200 and r.json()["abs_library_id"] is None


def test_account_scoped_matching(client, monkeypatch):
    """An account scoped to its own ABS library must not match (and auto-exclude
    on) a copy of the title that only exists in ANOTHER account's library."""
    with Session(engine) as session:
        main = session.exec(
            select(AudibleAccount).where(AudibleAccount.label == "ABS Test")
        ).one()
        main.abs_library_id = "lib-main"  # ABS1/ABS2 items live here
        session.add(main)
        other = AudibleAccount(
            label="ABS Test 2", status=AccountStatus.linked, abs_library_id="lib-other"
        )
        session.add(other)
        session.commit()
        session.refresh(other)
        # The same title the main account already has in ABS, owned by account 2.
        session.add(Book(audible_account_id=other.id, asin="ASINABS1", title="Already There"))
        session.commit()
        other_id = other.id

    # Scope-aware resolver: items only resolve inside lib-main.
    def scoped_resolve(base, token, book, library_id=None):
        return f"item-{book.asin}" if library_id == "lib-main" and book.asin in IN_ABS else None

    monkeypatch.setattr(abs_svc, "resolve_item_id", scoped_resolve)
    try:
        r = client.post("/api/library/abs-match")
        assert r.status_code == 200, r.text
        books = client.get("/api/library?limit=1000").json()
        mine = next(b for b in books if b["asin"] == "ASINABS1" and b["audible_account_id"] != other_id)
        theirs = next(b for b in books if b["asin"] == "ASINABS1" and b["audible_account_id"] == other_id)
        assert mine["abs_present"] is True and mine["excluded"] is True
        assert theirs["abs_present"] is False and theirs["excluded"] is False
    finally:
        with Session(engine) as session:
            for book in session.exec(select(Book).where(Book.audible_account_id == other_id)).all():
                session.delete(book)
            acct = session.get(AudibleAccount, other_id)
            if acct:
                session.delete(acct)
            main = session.exec(
                select(AudibleAccount).where(AudibleAccount.label == "ABS Test")
            ).one()
            main.abs_library_id = None
            session.add(main)
            session.commit()


def test_scoped_rematch_drops_cross_library_match(client, monkeypatch):
    """A book previously matched to an item in another account's library is
    unmatched (and its auto-exclusion lifted) once its account is scoped."""
    client.post("/api/library/abs-match")  # ASINABS1 matched + auto-excluded
    with Session(engine) as session:
        account = session.exec(
            select(AudibleAccount).where(AudibleAccount.label == "ABS Test")
        ).one()
        account.abs_library_id = "lib-empty"  # this account's own (empty) library
        session.add(account)
        session.commit()
    monkeypatch.setattr(
        abs_svc,
        "fetch_item_ids_by_library",
        lambda base, token: {
            "lib-main": {f"item-{a}" for a in IN_ABS},  # items are elsewhere
            "lib-empty": set(),
        },
    )
    # Nothing resolves inside lib-empty.
    monkeypatch.setattr(abs_svc, "resolve_item_id", lambda *a, **k: None)
    try:
        r = client.post("/api/library/abs-match")
        assert r.status_code == 200, r.text
        b = _book(client, "ASINABS1")
        assert b["abs_present"] is False
        assert b["excluded"] is False and b["abs_auto_excluded"] is False
        # Our own completed download is NOT flagged missing — the item still
        # exists in ABS, just outside this account's library.
        ours = _book(client, "ASINABS2")
        assert ours["abs_missing"] is False and ours["abs_present"] is False
    finally:
        with Session(engine) as session:
            account = session.exec(
                select(AudibleAccount).where(AudibleAccount.label == "ABS Test")
            ).one()
            account.abs_library_id = None
            session.add(account)
            session.commit()


def test_item_reappearing_clears_missing(client, monkeypatch):
    client.post("/api/library/abs-match")
    monkeypatch.setattr(
        abs_svc, "fetch_item_ids_by_library", lambda base, token: {"lib-main": {"item-ASINABS1"}}
    )
    monkeypatch.setattr(abs_svc, "resolve_item_id", lambda *a, **k: None)
    client.post("/api/library/abs-match")
    assert _book(client, "ASINABS2")["abs_missing"] is True

    # The item comes back (re-imported / rescanned) — the flag clears.
    monkeypatch.setattr(
        abs_svc,
        "fetch_item_ids_by_library",
        lambda base, token: {"lib-main": {f"item-{a}" for a in IN_ABS}},
    )
    monkeypatch.setattr(abs_svc, "resolve_item_id", _resolve_mock)
    client.post("/api/library/abs-match")
    ours = _book(client, "ASINABS2")
    assert ours["abs_missing"] is False and ours["abs_present"] is True
