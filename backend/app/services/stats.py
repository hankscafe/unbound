"""Aggregate counts for the dashboard and Homepage widget."""

from __future__ import annotations

from sqlmodel import Session, func, select

from app.db.models import AccountStatus, AudibleAccount, Book, DownloadJob, JobState

_IN_PROGRESS = {
    JobState.queued, JobState.downloading, JobState.downloaded,
    JobState.decrypting, JobState.tagging, JobState.moving,
}


def _count(session: Session, stmt) -> int:
    return session.exec(stmt).one()


def gather_stats(session: Session) -> dict:
    accounts_total = _count(session, select(func.count()).select_from(AudibleAccount))
    accounts_linked = _count(
        session,
        select(func.count()).select_from(AudibleAccount).where(
            AudibleAccount.status == AccountStatus.linked
        ),
    )
    books_total = _count(session, select(func.count()).select_from(Book))
    excluded = _count(
        session, select(func.count()).select_from(Book).where(Book.excluded == True)  # noqa: E712
    )

    def jobs_in(states) -> int:
        return _count(
            session,
            select(func.count()).select_from(DownloadJob).where(
                DownloadJob.state.in_(states)  # type: ignore[attr-defined]
            ),
        )

    downloaded = jobs_in([JobState.completed])
    in_progress = jobs_in(list(_IN_PROGRESS))
    failed = jobs_in([JobState.failed])
    # "pending" = owned, not excluded, no completed job yet.
    pending = max(books_total - excluded - downloaded, 0)

    return {
        "accounts_linked": accounts_linked,
        "accounts_total": accounts_total,
        "books_total": books_total,
        "downloaded": downloaded,
        "in_progress": in_progress,
        "failed": failed,
        "excluded": excluded,
        "pending": pending,
    }
