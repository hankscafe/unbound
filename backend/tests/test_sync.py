"""Library sync removal detection — offline (Audible API mocked).

Covers: a book Audible stops returning is flagged library_removed (with an event
for admins); it reappearing clears the flag; an empty fetch is treated as a bad
response, not a mass removal; downloads of removed books are refused with a clear
error; the flag surfaces through the library API.

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

from app.audible.client import LibraryItem  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models import (  # noqa: E402
    AccountStatus,
    AudibleAccount,
    Book,
    DownloadJob,
    EventLog,
    User,
    UserRole,
)
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.worker import tasks  # noqa: E402


def _item(asin: str, title: str) -> LibraryItem:
    return LibraryItem(
        asin=asin, title=title, subtitle=None, authors="Author", narrators=None,
        series=None, series_sequence=None, runtime_minutes=60, cover_url=None,
        purchase_date=None, is_aax_available=False, raw={},
    )


@pytest.fixture()
def client(monkeypatch):
    # Sync must not need real auth material.
    monkeypatch.setattr(tasks.account_svc, "load_authenticator", lambda account: object())
    with TestClient(app) as c:
        with Session(engine) as session:
            session.add(
                User(username="syncuser", email="sync@example.com",
                     password_hash=hash_password("pw-for-sync-tests"), role=UserRole.admin)
            )
            account = AudibleAccount(label="Sync Test", status=AccountStatus.linked,
                                     encrypted_auth_blob="blob")
            session.add(account)
            session.commit()
            session.refresh(account)
            books = [
                Book(audible_account_id=account.id, asin="SYNCKEEP", title="Still Owned"),
                Book(audible_account_id=account.id, asin="SYNCGONE", title="Removed Title"),
            ]
            session.add_all(books)
            session.commit()
        c.post("/api/auth/login", json={"username": "syncuser", "password": "pw-for-sync-tests"})
        yield c
    with Session(engine) as session:
        account = session.exec(
            select(AudibleAccount).where(AudibleAccount.label == "Sync Test")
        ).first()
        if account:
            books = session.exec(
                select(Book).where(Book.audible_account_id == account.id)
            ).all()
            for b in books:
                for j in session.exec(select(DownloadJob).where(DownloadJob.book_id == b.id)):
                    session.delete(j)
                session.delete(b)
            session.delete(account)
        for e in session.exec(select(EventLog).where(col(EventLog.message).contains("SYNC"))):
            session.delete(e)
        user = session.exec(select(User).where(User.username == "syncuser")).first()
        if user:
            session.delete(user)
        session.commit()


def _account_id(session: Session) -> int:
    return session.exec(
        select(AudibleAccount).where(AudibleAccount.label == "Sync Test")
    ).one().id


def _book(session: Session, asin: str) -> Book:
    return session.exec(select(Book).where(Book.asin == asin)).one()


def test_sync_flags_removed_books(client, monkeypatch):
    monkeypatch.setattr(tasks.ac, "fetch_library",
                        lambda auth: [_item("SYNCKEEP", "Still Owned")])
    with Session(engine) as session:
        account_id = _account_id(session)
    tasks.sync_account(account_id)
    with Session(engine) as session:
        gone = _book(session, "SYNCGONE")
        kept = _book(session, "SYNCKEEP")
        assert gone.library_removed and gone.library_removed_at is not None
        assert not kept.library_removed
        # Admins get a warning event naming the title.
        ev = session.exec(
            select(EventLog).where(col(EventLog.message).contains("SYNCGONE"))
        ).first()
        assert ev is not None and ev.level == "warning"


def test_sync_clears_flag_when_book_returns(client, monkeypatch):
    monkeypatch.setattr(tasks.ac, "fetch_library",
                        lambda auth: [_item("SYNCKEEP", "Still Owned")])
    with Session(engine) as session:
        account_id = _account_id(session)
    tasks.sync_account(account_id)
    monkeypatch.setattr(
        tasks.ac, "fetch_library",
        lambda auth: [_item("SYNCKEEP", "Still Owned"), _item("SYNCGONE", "Removed Title")],
    )
    tasks.sync_account(account_id)
    with Session(engine) as session:
        gone = _book(session, "SYNCGONE")
        assert not gone.library_removed and gone.library_removed_at is None


def test_empty_fetch_is_not_a_mass_removal(client, monkeypatch):
    monkeypatch.setattr(tasks.ac, "fetch_library", lambda auth: [])
    with Session(engine) as session:
        account_id = _account_id(session)
    tasks.sync_account(account_id)
    with Session(engine) as session:
        assert not _book(session, "SYNCKEEP").library_removed
        assert not _book(session, "SYNCGONE").library_removed


def test_denial_message_includes_structured_reasons():
    from app.audible.client import _denial_message

    cl = {
        "message": "License not granted to customer [X] for asin [B00BWQUFRE]",
        "license_denial_reasons": [
            {"rejectionReason": "OwnershipDenialReason", "validationType": "Ownership"},
            {"validationType": "Membership"},
        ],
    }
    out = _denial_message(cl, "B00BWQUFRE")
    assert "License not granted" in out
    assert "OwnershipDenialReason / Ownership" in out and "Membership" in out
    # No reasons → just the message.
    assert _denial_message({}, "X") == "License denied for X"


def test_license_hint_detects_throttling():
    msg = ("License not granted to customer [X] for asin [Y] "
           "(denial reasons: CustomerThrottled / Ownership)")
    out, resync = tasks._license_hint(msg)
    assert resync is False
    assert "rate-limiting" in out and "24-48 hours" in out
    # Any other denial still triggers the refresh sync.
    out, resync = tasks._license_hint("License denied for Y")
    assert resync is True and "re-syncing" in out


def test_throttle_cooldown_blocks_downloads_locally(client, monkeypatch):
    import time as time_mod

    from app.db.models import DownloadJob, JobState

    monkeypatch.setattr(tasks.network, "ensure_online", lambda: True)
    # Audible must never be contacted while the account is cooling down.
    monkeypatch.setattr(tasks.ac, "get_aaxc_license",
                        lambda *a, **k: pytest.fail("license requested during cooldown"))
    with Session(engine) as session:
        account_id = _account_id(session)
        book_id = _book(session, "SYNCKEEP").id
        job = DownloadJob(book_id=book_id, state=JobState.queued)
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id
    monkeypatch.setitem(tasks._throttled_until, account_id, time_mod.monotonic() + 600)
    tasks.download_book(job_id)
    with Session(engine) as session:
        job = session.get(DownloadJob, job_id)
        assert job.state == JobState.failed
        assert "Skipped without contacting Audible" in job.error_message


def test_download_refused_when_rights_restricted(client):
    with Session(engine) as session:
        account_id = _account_id(session)
        b = Book(audible_account_id=account_id, asin="SYNCPLUS", title="Streaming Only",
                 raw_metadata={"customer_rights": {"is_consumable_offline": False}})
        session.add(b)
        session.commit()
        session.refresh(b)
        bid = b.id
    r = client.post(f"/api/library/{bid}/download")
    assert r.status_code == 400
    assert "not downloadable" in r.json()["detail"]
    r = client.get(f"/api/library/{bid}")
    assert r.status_code == 200
    assert r.json()["download_restricted"] is True


def test_download_refused_for_removed_book(client, monkeypatch):
    monkeypatch.setattr(tasks.ac, "fetch_library",
                        lambda auth: [_item("SYNCKEEP", "Still Owned")])
    with Session(engine) as session:
        account_id = _account_id(session)
    tasks.sync_account(account_id)
    with Session(engine) as session:
        gone_id = _book(session, "SYNCGONE").id
    r = client.post(f"/api/library/{gone_id}/download")
    assert r.status_code == 400
    assert "no longer in the Audible library" in r.json()["detail"]
    # And the flag is visible to the UI.
    r = client.get(f"/api/library/{gone_id}")
    assert r.status_code == 200
    assert r.json()["library_removed"] is True
