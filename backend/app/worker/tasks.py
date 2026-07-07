"""Background tasks: library sync and the download→decrypt→tag→move pipeline."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlmodel import Session, select

from app.audible import client as ac
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import (
    AccountStatus,
    AudibleAccount,
    Book,
    DownloadJob,
    EventLog,
    JobState,
    LibraryProfile,
)
from app.db.session import engine
from app.pipeline import chapters as chapters_mod
from app.pipeline import decrypt, download, mover, tagging
from app.services import accounts as account_svc
from app.services import events, network, notifier
from app.db import init_db
from app.worker.queue import task

log = get_logger("worker.tasks")
settings = get_settings()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_URL_QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]*")


def _safe_error(exc: Exception) -> str:
    """Stringify an error with signed-URL query strings redacted (no secret leakage)."""
    return _URL_QUERY.sub(r"\1?<redacted>", str(exc))[:1000]


# HTTP statuses worth retrying: expired signed URL, request timeout, rate limit, 5xx.
_RETRY_STATUS = {403, 408, 425, 429, 500, 502, 503, 504}
_MAX_DOWNLOAD_ATTEMPTS = 4


def _transient(exc: Exception) -> bool:
    """Whether a download error is worth retrying (network blips, expired URL, 5xx)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRY_STATUS
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


def _is_auth_error(exc: Exception) -> bool:
    """True only for genuine authentication failures (expired/revoked tokens).

    Those warrant re-linking; other API errors (bad request, network, server) do not.
    """
    try:
        from audible.exceptions import NoRefreshToken, Unauthorized

        if isinstance(exc, (Unauthorized, NoRefreshToken)):
            return True
    except Exception:
        pass
    return getattr(exc, "code", None) in (401, 403)


def _emit(job: DownloadJob) -> None:
    events.publish(
        {
            "type": "job",
            "job_id": job.id,
            "book_id": job.book_id,
            "state": job.state.value,
            "progress": round(job.progress, 1),
            "bytes_done": job.bytes_done,
            "bytes_total": job.bytes_total,
        }
    )


def _set_state(session: Session, job: DownloadJob, state: JobState, **fields) -> None:
    job.state = state
    job.updated_at = _utcnow()
    for k, v in fields.items():
        setattr(job, k, v)
    session.add(job)
    session.commit()
    session.refresh(job)
    _emit(job)


# --- Library sync ----------------------------------------------------------


@task("sync_account")
def sync_account(account_id: int) -> None:
    with Session(engine) as session:
        account = session.get(AudibleAccount, account_id)
        # Sync as long as we hold auth material — a prior non-auth failure may have
        # left the account in needs_reauth/error, but the token can still be valid.
        if account is None or not account.encrypted_auth_blob:
            return
        try:
            auth = account_svc.load_authenticator(account)
            # Backfill the owner display (email/name) for accounts linked before this
            # was captured, or when it was previously unavailable.
            if not account.account_owner_email:
                ci = getattr(auth, "customer_info", None) or {}
                if isinstance(ci, dict):
                    account.account_owner_email = (
                        ci.get("email") or ci.get("name") or ci.get("given_name")
                    )
            items = ac.fetch_library(auth)
        except Exception as exc:
            # Only a genuine auth failure means the user must re-link. Any other error
            # (bad request, network, server) leaves the link intact — just record it.
            is_auth_error = _is_auth_error(exc)
            account.status = AccountStatus.needs_reauth if is_auth_error else AccountStatus.linked
            account.last_error = _safe_error(exc)
            session.add(account)
            session.add(EventLog(level="error", category="account", account_id=account_id,
                                 message=f"Sync failed: {account.last_error}"))
            session.commit()
            events.publish({"type": "account", "account_id": account_id, "status": account.status.value})
            return

        existing = {
            b.asin: b
            for b in session.exec(select(Book).where(Book.audible_account_id == account_id)).all()
        }
        added = 0
        for item in items:
            book = existing.get(item.asin)
            if book is None:
                book = Book(audible_account_id=account_id, asin=item.asin, title=item.title)
                added += 1
            book.title = item.title
            book.subtitle = item.subtitle
            book.authors = item.authors
            book.narrators = item.narrators
            book.series = item.series
            book.series_sequence = item.series_sequence
            book.runtime_minutes = item.runtime_minutes
            book.cover_url = item.cover_url
            book.is_aax_available = item.is_aax_available
            book.raw_metadata = item.raw
            book.updated_at = _utcnow()
            session.add(book)

        account.last_sync_at = _utcnow()
        account.last_error = None
        account.status = AccountStatus.linked  # a successful sync clears any prior error state
        session.add(account)
        session.add(EventLog(category="account", account_id=account_id,
                             message=f"Sync complete: {len(items)} items ({added} new)"))
        session.commit()
        events.publish({"type": "sync_complete", "account_id": account_id, "count": len(items)})
        events.publish({"type": "account", "account_id": account_id, "status": account.status.value})
        log.info("sync_complete", account_id=account_id, count=len(items), added=added)
        if added:
            notifier.send_if_enabled(
                init_db.SETTING_NOTIFY_NEW_BOOKS,
                "Unbound: new audiobooks found",
                f"{added} new title(s) in '{account.label}'.",
            )


# --- Download pipeline -----------------------------------------------------


def _select_profile(session: Session, book: Book) -> LibraryProfile | None:
    # Prefer an account-scoped profile, else the default, else any profile.
    account_scoped = session.exec(
        select(LibraryProfile).where(LibraryProfile.audible_account_id == book.audible_account_id)
    ).first()
    if account_scoped:
        return account_scoped
    default = session.exec(
        select(LibraryProfile).where(LibraryProfile.is_default == True)  # noqa: E712
    ).first()
    return default or session.exec(select(LibraryProfile)).first()


@task("download_book")
def download_book(job_id: int) -> None:
    with Session(engine) as session:
        job = session.get(DownloadJob, job_id)
        if job is None:
            return
        if job.state != JobState.queued:
            return  # cancelled meanwhile, or another worker already picked it up
        if not network.ensure_online():
            # Park (stays queued in the DB, out of the worker pool); the scheduler
            # re-enqueues every parked job the moment connectivity returns.
            log.info("download_deferred_offline", job_id=job_id)
            return
        book = session.get(Book, job.book_id)
        if book is None:
            _set_state(session, job, JobState.failed, error_message="Book not found")
            return
        if book.excluded:
            _set_state(session, job, JobState.excluded)
            return
        account = session.get(AudibleAccount, book.audible_account_id)
        if account is None or account.status != AccountStatus.linked:
            _set_state(session, job, JobState.failed, error_message="Account not linked")
            return
        profile = _select_profile(session, book)
        if profile is None:
            _set_state(session, job, JobState.failed,
                       error_message="No library profile configured")
            return

        job.attempt_count += 1
        job.started_at = _utcnow()
        session.add(job)
        session.commit()

        try:
            auth = account_svc.load_authenticator(account)

            # The downloader reports every 256KB chunk; persisting/broadcasting each
            # one would hammer SQLite and the SSE stream, so throttle to ~1/second
            # (always letting the final chunk through).
            _last_emit = {"t": 0.0}

            def on_progress(done: int, total: int | None) -> None:
                now = time.monotonic()
                if total and done < total and now - _last_emit["t"] < 1.0:
                    return
                _last_emit["t"] = now
                job.bytes_done = done
                job.bytes_total = total
                job.progress = (done / total * 100.0) if total else 0.0
                job.updated_at = _utcnow()
                session.add(job)
                session.commit()
                _emit(job)

            # 1) Download the encrypted file, retrying transient failures with backoff.
            #    The voucher key/iv are deterministic per (device, asin), so re-fetching
            #    the license on each attempt yields a fresh signed URL that can resume the
            #    same .part. Download extension follows the license format.
            enc_path = settings.downloads_dir / f"{book.asin}.aaxc"
            license = None
            for attempt in range(1, _MAX_DOWNLOAD_ATTEMPTS + 1):
                try:
                    license = ac.get_aaxc_license(auth, book.asin)
                    _set_state(session, job, JobState.downloading, format=license.format,
                               source_path=str(enc_path))
                    download.download_file(license.download_url, enc_path, on_progress=on_progress)
                    break
                except Exception as exc:
                    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
                        # Transport failure = the network itself is suspect. Park the
                        # job (the .part resumes later) instead of burning retries;
                        # recovery re-enqueues it automatically.
                        network.mark_down(_safe_error(exc))
                        log.warning("download_parked_offline", asin=book.asin)
                        _set_state(session, job, JobState.queued)
                        return
                    if attempt == _MAX_DOWNLOAD_ATTEMPTS or not _transient(exc):
                        raise
                    wait = min(60, 2 ** attempt)
                    log.warning("download_retry", asin=book.asin, attempt=attempt,
                                wait=wait, error=_safe_error(exc))
                    time.sleep(wait)
            _set_state(session, job, JobState.downloaded, progress=100.0)

            # 2) Fetch chapter markers (best-effort — never fail the download over them).
            chapters_file = None
            if profile.embed_chapters:
                try:
                    chs = ac.get_chapters(auth, book.asin)
                    if chs:
                        chapters_file = settings.downloads_dir / f"{book.asin}.ffmeta"
                        chapters_mod.write_ffmeta(chs, chapters_file)
                        log.info("chapters_fetched", asin=book.asin, count=len(chs))
                except Exception as exc:
                    log.warning("chapters_failed", asin=book.asin, error=_safe_error(exc))

            # 3) Decrypt (embedding chapters if available).
            _set_state(session, job, JobState.decrypting)
            dec_path = settings.downloads_dir / f"{book.asin}.m4b"
            if license and license.key and license.iv:
                decrypt.decrypt_aaxc(enc_path, dec_path, key=license.key, iv=license.iv,
                                     chapters_file=chapters_file)
            else:
                ab = account_svc.load_activation_bytes(account)
                if not ab:
                    ab = ac.get_activation_bytes(auth)
                    account_svc.store_activation_bytes(account, ab)
                    session.add(account)
                    session.commit()
                decrypt.decrypt_aax(enc_path, dec_path, activation_bytes=ab,
                                    chapters_file=chapters_file)

            # 4) Tag.
            _set_state(session, job, JobState.tagging)
            if profile.embed_cover:
                tagging.apply_tags(dec_path, book, embed_cover=True)

            # 5) Move to library.
            _set_state(session, job, JobState.moving)
            final = mover.move_to_library(dec_path, book, profile)

            # Cleanup intermediates.
            for p in (enc_path, chapters_file):
                if p:
                    try:
                        Path(p).unlink(missing_ok=True)
                    except OSError:
                        pass

            _set_state(session, job, JobState.completed, progress=100.0,
                       output_path=str(final), finished_at=_utcnow(), library_profile_id=profile.id)
            session.add(EventLog(category="job", job_id=job.id, book_id=book.id,
                                 message=f"Completed: {book.title}"))
            session.commit()
            log.info("download_completed", asin=book.asin, path=str(final))
            notifier.send_if_enabled(
                init_db.SETTING_NOTIFY_COMPLETE, "Unbound: download complete", book.title
            )

        except Exception as exc:
            safe = _safe_error(exc)
            log.error("download_failed", asin=book.asin, error=safe)
            _set_state(session, job, JobState.failed, error_message=safe, finished_at=_utcnow())
            session.add(EventLog(level="error", category="job", job_id=job.id, book_id=book.id,
                                 message=f"Failed: {book.title}: {safe}"))
            session.commit()
            notifier.send_if_enabled(
                init_db.SETTING_NOTIFY_FAILURE, "Unbound: download failed",
                f"{book.title}\n{safe}",
            )


def requeue_parked_jobs() -> None:
    """Re-enqueue jobs parked while the network was down (scheduler, on recovery)."""
    from app.worker.queue import enqueue

    with Session(engine) as session:
        parked = session.exec(
            select(DownloadJob).where(DownloadJob.state == JobState.queued)
        ).all()
    for job in parked:
        enqueue("download_book", job_id=job.id)
    if parked:
        log.info("requeued_after_network_recovery", count=len(parked))


def requeue_incomplete_jobs() -> None:
    """On startup, re-enqueue jobs left mid-flight by a restart."""
    from app.worker.queue import enqueue

    active = {
        JobState.queued, JobState.downloading, JobState.downloaded,
        JobState.decrypting, JobState.tagging, JobState.moving,
    }
    with Session(engine) as session:
        stuck = session.exec(select(DownloadJob).where(DownloadJob.state.in_(active))).all()  # type: ignore[attr-defined]
        for job in stuck:
            job.state = JobState.queued
            session.add(job)
        session.commit()
        for job in stuck:
            enqueue("download_book", job_id=job.id)
        if stuck:
            log.info("requeued_jobs", count=len(stuck))
