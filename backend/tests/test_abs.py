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
    # All matched items exist in ABS unless a test says otherwise.
    monkeypatch.setattr(
        abs_svc, "fetch_all_item_ids", lambda base, token: {f"item-{a}" for a in IN_ABS}
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
    monkeypatch.setattr(abs_svc, "fetch_all_item_ids", lambda base, token: {"item-ASINABS1"})
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
    monkeypatch.setattr(abs_svc, "fetch_all_item_ids", lambda base, token: {"item-ASINABS2"})
    monkeypatch.setattr(abs_svc, "resolve_item_id", lambda *a, **k: None)
    client.post("/api/library/abs-match")
    b = _book(client, "ASINABS1")
    assert b["excluded"] is False and b["abs_auto_excluded"] is False
    assert b["abs_missing"] is False  # we never downloaded it, so it is not "missing"


def test_empty_abs_item_list_is_not_a_mass_removal(client, monkeypatch):
    client.post("/api/library/abs-match")
    monkeypatch.setattr(abs_svc, "fetch_all_item_ids", lambda base, token: set())
    client.post("/api/library/abs-match")
    ours = _book(client, "ASINABS2")
    assert ours["abs_missing"] is False and ours["abs_present"] is True


def test_item_reappearing_clears_missing(client, monkeypatch):
    client.post("/api/library/abs-match")
    monkeypatch.setattr(abs_svc, "fetch_all_item_ids", lambda base, token: {"item-ASINABS1"})
    monkeypatch.setattr(abs_svc, "resolve_item_id", lambda *a, **k: None)
    client.post("/api/library/abs-match")
    assert _book(client, "ASINABS2")["abs_missing"] is True

    # The item comes back (re-imported / rescanned) — the flag clears.
    monkeypatch.setattr(
        abs_svc, "fetch_all_item_ids", lambda base, token: {f"item-{a}" for a in IN_ABS}
    )
    monkeypatch.setattr(abs_svc, "resolve_item_id", _resolve_mock)
    client.post("/api/library/abs-match")
    ours = _book(client, "ASINABS2")
    assert ours["abs_missing"] is False and ours["abs_present"] is True
