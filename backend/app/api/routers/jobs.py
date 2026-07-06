"""Download job listing, retry, and cancel."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, col, select

from app.api.deps import db_session, require_admin
from app.db.models import Book, DownloadJob, JobState
from app.schemas import JobOut
from app.worker.queue import enqueue

router = APIRouter(tags=["jobs"], dependencies=[Depends(require_admin)])

_ACTIVE = {
    JobState.queued, JobState.downloading, JobState.downloaded,
    JobState.decrypting, JobState.tagging, JobState.moving,
}


def _to_out(session: Session, job: DownloadJob) -> JobOut:
    book = session.get(Book, job.book_id)
    return JobOut(
        id=job.id,  # type: ignore[arg-type]
        book_id=job.book_id,
        book_title=book.title if book else None,
        state=job.state.value,
        progress=job.progress,
        format=job.format,
        error_message=job.error_message,
        attempt_count=job.attempt_count,
        updated_at=job.updated_at,
    )


@router.get("", response_model=list[JobOut])
def list_jobs(
    session: Session = Depends(db_session),
    state: str | None = None,
    limit: int = 200,
) -> list[JobOut]:
    stmt = select(DownloadJob)
    if state:
        stmt = stmt.where(DownloadJob.state == JobState(state))
    stmt = stmt.order_by(col(DownloadJob.updated_at).desc()).limit(limit)
    return [_to_out(session, j) for j in session.exec(stmt).all()]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, session: Session = Depends(db_session)) -> JobOut:
    job = session.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return _to_out(session, job)


@router.post("/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: int, session: Session = Depends(db_session)) -> JobOut:
    job = session.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.state not in (JobState.failed, JobState.cancelled):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only failed/cancelled jobs can be retried")
    job.state = JobState.queued
    job.error_message = None
    job.progress = 0.0
    session.add(job)
    session.commit()
    enqueue("download_book", job_id=job.id)
    return _to_out(session, job)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int, session: Session = Depends(db_session)) -> JobOut:
    job = session.get(DownloadJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.state not in _ACTIVE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Job is not active")
    # Cooperative cancel: mark cancelled; the pipeline checks state at each stage.
    job.state = JobState.cancelled
    session.add(job)
    session.commit()
    return _to_out(session, job)
