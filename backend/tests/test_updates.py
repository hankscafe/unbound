"""Update-check announcement tests — offline (GitHub call monkeypatched).

Like test_oidc, this module shares the app DB with the other test files, so it
cleans up every row/setting it creates (test_smoke asserts first-run state).
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

from app import __version__  # noqa: E402
from app.db import init_db  # noqa: E402
from app.db.models import AppSetting, EventLog  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import updates  # noqa: E402


@pytest.fixture()
def db():
    with TestClient(app):  # lifespan ensures tables exist
        yield
    with Session(engine) as session:
        row = session.get(AppSetting, init_db.SETTING_LAST_NOTIFIED_VERSION)
        if row:
            session.delete(row)
        for e in session.exec(
            select(EventLog).where(col(EventLog.message).like("Update available:%"))
        ).all():
            session.delete(e)
        session.commit()


def test_status_exposes_github_url(db):
    with TestClient(app) as client:
        status = client.get("/api/auth/status").json()
        assert status["github_url"].startswith("https://github.com/")
        assert status["version"] == __version__


def test_check_and_notify_announces_once_per_version(db, monkeypatch):
    sent: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(
        updates,
        "check_for_update",
        lambda force=False: {
            "current_version": __version__,
            "latest_version": "v99.0.0",
            "update_available": True,
        },
    )
    from app.services import notifier

    monkeypatch.setattr(
        notifier,
        "send_if_enabled",
        lambda key, title, body, default=False: sent.append((key, title, default)),
    )

    result = updates.check_and_notify()
    assert result["update_available"] is True
    assert len(sent) == 1
    assert sent[0][0] == init_db.SETTING_NOTIFY_UPDATE
    assert "v99.0.0" in sent[0][1]
    assert sent[0][2] is True  # announcements default to on

    with Session(engine) as session:
        assert init_db.get_setting(session, init_db.SETTING_LAST_NOTIFIED_VERSION) == "v99.0.0"
        events = session.exec(
            select(EventLog).where(col(EventLog.message).like("Update available:%"))
        ).all()
        assert len(events) == 1

    # Same version again → no duplicate announcement.
    updates.check_and_notify()
    assert len(sent) == 1

    # A newer version → announced again.
    monkeypatch.setattr(
        updates,
        "check_for_update",
        lambda force=False: {
            "current_version": __version__,
            "latest_version": "v99.1.0",
            "update_available": True,
        },
    )
    updates.check_and_notify()
    assert len(sent) == 2 and "v99.1.0" in sent[1][1]


def test_no_announcement_when_up_to_date(db, monkeypatch):
    monkeypatch.setattr(
        updates,
        "check_for_update",
        lambda force=False: {
            "current_version": __version__,
            "latest_version": f"v{__version__}",
            "update_available": False,
        },
    )
    from app.services import notifier

    monkeypatch.setattr(
        notifier,
        "send_if_enabled",
        lambda *a, **k: pytest.fail("must not notify when up to date"),
    )
    updates.check_and_notify()
    with Session(engine) as session:
        assert init_db.get_setting(session, init_db.SETTING_LAST_NOTIFIED_VERSION) is None
