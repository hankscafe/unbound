"""Network-down detection: state machine + parked-download resume — offline
(the connectivity probe is monkeypatched).

Shares the app DB with the other test modules; cleans up everything it creates.
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
from sqlmodel import Session, col, select  # noqa: E402

from app.db.models import EventLog  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import network  # noqa: E402


@pytest.fixture()
def net():
    with TestClient(app):  # lifespan ensures tables exist for transition EventLogs
        yield network
    # Reset module state and remove the transition log rows we caused.
    network._online = True
    network._last_probe = -network.PROBE_INTERVAL
    with Session(engine) as session:
        for e in session.exec(
            select(EventLog).where(col(EventLog.message).like("Network %"))
        ).all():
            session.delete(e)
        session.commit()


def test_starts_online(net):
    assert net.is_online() is True
    assert net.ensure_online() is True
    assert net.recheck_and_resume() is False  # nothing to resume while up


def test_mark_down_parks_and_probe_recovers(net, monkeypatch):
    net.mark_down("connection reset")
    assert net.is_online() is False
    # Freshly marked down → within the probe interval nothing re-probes.
    assert net.ensure_online() is False

    # Force the interval to have elapsed; a failing probe keeps us down...
    # (relative to monotonic "now" — on fresh CI VMs monotonic() can be < 60s,
    # so an absolute 0.0 would NOT count as elapsed)
    monkeypatch.setattr(net, "probe", lambda: False)
    net._last_probe = time.monotonic() - net.PROBE_INTERVAL - 1
    assert net.ensure_online() is False
    assert net.is_online() is False

    # ...and a succeeding probe flips us back up, exactly once signalling resume.
    monkeypatch.setattr(net, "probe", lambda: True)
    net._last_probe = time.monotonic() - net.PROBE_INTERVAL - 1
    assert net.recheck_and_resume() is True  # down → up transition
    assert net.is_online() is True
    assert net.recheck_and_resume() is False  # already up — no double resume

    # Both transitions were recorded for the activity feed.
    with Session(engine) as session:
        messages = [
            e.message
            for e in session.exec(
                select(EventLog).where(col(EventLog.message).like("Network %"))
            ).all()
        ]
    assert any("down" in m for m in messages)
    assert any("restored" in m for m in messages)


def test_mark_down_is_idempotent(net):
    net.mark_down("first")
    net.mark_down("second")
    with Session(engine) as session:
        downs = session.exec(
            select(EventLog).where(col(EventLog.message).like("Network appears%"))
        ).all()
    assert len(downs) == 1  # one transition, one log entry — no spam
