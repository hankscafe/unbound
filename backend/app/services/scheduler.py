"""Background scheduler: periodic library sync + optional auto-download.

A single daemon thread wakes on a short tick, re-reads the schedule settings each
cycle (so changes apply without a restart), and when the configured interval has
elapsed it syncs every linked account and — if enabled — queues downloads for any
owned, non-excluded titles that have never been downloaded.
"""

from __future__ import annotations

import threading
import time

from sqlmodel import Session, select

from app.core.logging import get_logger
from app.db import init_db
from app.db.models import AccountStatus, AudibleAccount, Book, DownloadJob, JobState
from app.db.session import engine

log = get_logger("services.scheduler")

_TICK_SECONDS = 60.0
_UPDATE_CHECK_SECONDS = 6 * 3600.0  # matches the update-check cache TTL
_stop = threading.Event()
_thread: threading.Thread | None = None
_last_run: float = 0.0
_last_update_check: float = 0.0


def _run_cycle() -> None:
    from app.services import network
    from app.worker.queue import enqueue
    from app.worker.tasks import sync_account

    if not network.ensure_online():
        # No point syncing or queueing downloads into a dead network; the per-tick
        # recovery check below resumes everything once connectivity returns.
        log.info("scheduler_cycle_skipped_offline")
        return

    with Session(engine) as session:
        auto_download = init_db.get_bool(session, init_db.SETTING_AUTO_DOWNLOAD)
        accounts = session.exec(
            select(AudibleAccount).where(AudibleAccount.status == AccountStatus.linked)
        ).all()
        account_ids = [a.id for a in accounts]

    log.info("scheduler_cycle_start", accounts=len(account_ids), auto_download=auto_download)
    for aid in account_ids:
        sync_account(aid)  # synchronous within the scheduler thread

    # Periodic AudiobookShelf matching (by now ABS has had time to scan new files).
    try:
        from app.services import audiobookshelf as abs_svc

        with Session(engine) as session:
            abs_svc.match_all(session)
    except Exception as exc:  # pragma: no cover
        log.warning("scheduler_abs_match_failed", error=str(exc))

    if not auto_download:
        return

    # Queue downloads for titles that have never been attempted.
    with Session(engine) as session:
        books = session.exec(select(Book).where(Book.excluded == False)).all()  # noqa: E712
        queued = 0
        for book in books:
            has_job = session.exec(
                select(DownloadJob).where(DownloadJob.book_id == book.id).limit(1)
            ).first()
            if has_job is not None:
                continue
            job = DownloadJob(book_id=book.id, state=JobState.queued)
            session.add(job)
            session.commit()
            session.refresh(job)
            enqueue("download_book", job_id=job.id)
            queued += 1
    if queued:
        log.info("scheduler_auto_download", queued=queued)


def _loop() -> None:
    global _last_run, _last_update_check
    _last_run = time.time()  # don't fire immediately on boot
    while not _stop.is_set():
        try:
            with Session(engine) as session:
                enabled = init_db.get_bool(session, init_db.SETTING_SCHEDULE_ENABLED)
                interval_h = int(
                    init_db.get_setting(session, init_db.SETTING_SCHEDULE_INTERVAL_HOURS, "12")
                )
            if enabled and (time.time() - _last_run) >= interval_h * 3600:
                _last_run = time.time()
                _run_cycle()
        except Exception as exc:  # pragma: no cover - keep the loop alive
            log.error("scheduler_error", error=str(exc))
        try:
            # While the network is down, probe each tick; on recovery, resume the
            # downloads that were parked (they stayed 'queued' in the DB).
            from app.services import network

            if network.recheck_and_resume():
                from app.worker.tasks import requeue_parked_jobs

                requeue_parked_jobs()
        except Exception as exc:  # pragma: no cover - keep the loop alive
            log.error("network_recovery_error", error=str(exc))
        try:
            # Update announcements run regardless of the library schedule — admins
            # should hear about new versions even with automation switched off.
            if (time.time() - _last_update_check) >= _UPDATE_CHECK_SECONDS or _last_update_check == 0:
                _last_update_check = time.time()
                from app.services import updates

                updates.check_and_notify()
        except Exception as exc:  # pragma: no cover - keep the loop alive
            log.error("update_check_error", error=str(exc))
        _stop.wait(_TICK_SECONDS)


def start_scheduler() -> None:
    global _thread
    if _thread is not None:
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="unbound-scheduler", daemon=True)
    _thread.start()
    log.info("scheduler_started")


def stop_scheduler() -> None:
    _stop.set()
