"""Library browsing, exclude toggle, and download triggers."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, col, or_, select

from urllib.parse import quote

from app.api.deps import db_session, require_admin
from app.audible.marketplace import audible_product_url
from app.db import init_db
from app.db.models import (
    AccountStatus,
    AudibleAccount,
    Book,
    DownloadJob,
    EventLog,
    JobState,
)
from app.schemas import BatchDownload, BatchExclude, BookOut, ExcludeRequest
from app.worker.queue import enqueue

router = APIRouter(tags=["library"], dependencies=[Depends(require_admin)])


def _latest_job(session: Session, book_id: int) -> DownloadJob | None:
    return session.exec(
        select(DownloadJob)
        .where(DownloadJob.book_id == book_id)
        .order_by(col(DownloadJob.id).desc())
    ).first()


def _abs_link(session: Session, book: Book) -> str | None:
    """'Open in AudiobookShelf' link: exact item deep-link if matched, else title search."""
    base = (init_db.get_setting(session, init_db.SETTING_ABS_URL) or "").rstrip("/")
    if not base:
        return None
    if book.abs_item_id:
        return f"{base}/item/{book.abs_item_id}"
    lib = init_db.get_setting(session, init_db.SETTING_ABS_LIBRARY_ID)
    if lib:
        return f"{base}/library/{lib}/search?q={quote(book.title)}"
    return base


def _to_out(session: Session, book: Book, accounts: dict[int, AudibleAccount]) -> BookOut:
    account = accounts.get(book.audible_account_id)
    job = _latest_job(session, book.id)  # type: ignore[arg-type]
    completed = job is not None and job.state == JobState.completed
    return BookOut(
        id=book.id,  # type: ignore[arg-type]
        audible_account_id=book.audible_account_id,
        account_label=account.label if account else None,
        account_badge_color=account.badge_color if account else None,
        asin=book.asin,
        title=book.title,
        subtitle=book.subtitle,
        authors=book.authors,
        narrators=book.narrators,
        series=book.series,
        series_sequence=book.series_sequence,
        runtime_minutes=book.runtime_minutes,
        cover_url=book.cover_url,
        is_aax_available=book.is_aax_available,
        excluded=book.excluded,
        purchase_date=book.purchase_date,
        audible_url=audible_product_url(book.asin, account.marketplace if account else "us"),
        abs_url=_abs_link(session, book) if (completed or book.abs_item_id) else None,
        abs_present=book.abs_item_id is not None,
        abs_auto_excluded=book.abs_auto_excluded,
        output_path=job.output_path if completed else None,
        job_state=job.state.value if job else None,
        job_progress=job.progress if job else None,
    )


@router.get("", response_model=list[BookOut])
def list_library(
    session: Session = Depends(db_session),
    account_id: int | None = None,
    excluded: bool | None = None,
    search: str | None = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
) -> list[BookOut]:
    stmt = select(Book)
    if account_id is not None:
        stmt = stmt.where(Book.audible_account_id == account_id)
    if excluded is not None:
        stmt = stmt.where(Book.excluded == excluded)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(col(Book.title).ilike(like), col(Book.authors).ilike(like),
                col(Book.series).ilike(like))
        )
    stmt = stmt.order_by(col(Book.title)).offset(offset).limit(limit)
    books = session.exec(stmt).all()
    accounts = {a.id: a for a in session.exec(select(AudibleAccount)).all()}
    return [_to_out(session, b, accounts) for b in books]


@router.get("/{book_id}", response_model=BookOut)
def get_book(book_id: int, session: Session = Depends(db_session)) -> BookOut:
    book = session.get(Book, book_id)
    if book is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Book not found")
    accounts = {a.id: a for a in session.exec(select(AudibleAccount)).all()}
    return _to_out(session, book, accounts)


@router.post("/{book_id}/exclude", response_model=BookOut)
def set_excluded(
    book_id: int, payload: ExcludeRequest, session: Session = Depends(db_session)
) -> BookOut:
    book = session.get(Book, book_id)
    if book is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Book not found")
    _apply_exclude(book, payload.excluded)
    session.add(book)
    session.add(EventLog(category="library", book_id=book_id,
                         message=f"{'Excluded' if payload.excluded else 'Included'}: {book.title}"))
    session.commit()
    accounts = {a.id: a for a in session.exec(select(AudibleAccount)).all()}
    return _to_out(session, book, accounts)


def _apply_exclude(book: Book, excluded: bool) -> None:
    """Set the exclude flag; re-including a book we auto-excluded (already in
    AudiobookShelf) is remembered so it's never auto-excluded again."""
    if not excluded and book.abs_auto_excluded:
        book.abs_auto_excluded = False
        book.abs_exclude_override = True
    book.excluded = excluded


def _queue_download(session: Session, book: Book) -> DownloadJob | None:
    if book.excluded:
        return None
    account = session.get(AudibleAccount, book.audible_account_id)
    if account is None or account.status != AccountStatus.linked:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Account for '{book.title}' is not linked")
    job = DownloadJob(book_id=book.id, state=JobState.queued)  # type: ignore[arg-type]
    session.add(job)
    session.commit()
    session.refresh(job)
    enqueue("download_book", job_id=job.id)
    return job


@router.post("/{book_id}/download", status_code=status.HTTP_202_ACCEPTED)
def download_book(book_id: int, session: Session = Depends(db_session)) -> dict:
    book = session.get(Book, book_id)
    if book is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Book not found")
    job = _queue_download(session, book)
    if job is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Book is excluded")
    return {"job_id": job.id, "state": job.state.value}


@router.post("/download-all", status_code=status.HTTP_202_ACCEPTED)
def download_all(
    session: Session = Depends(db_session), account_id: int | None = None
) -> dict:
    stmt = select(Book).where(Book.excluded == False)  # noqa: E712
    if account_id is not None:
        stmt = stmt.where(Book.audible_account_id == account_id)
    books = session.exec(stmt).all()
    queued = 0
    for book in books:
        # Skip books already completed.
        latest = _latest_job(session, book.id)  # type: ignore[arg-type]
        if latest and latest.state == JobState.completed:
            continue
        try:
            if _queue_download(session, book):
                queued += 1
        except HTTPException:
            continue
    return {"queued": queued}


@router.post("/batch/exclude")
def batch_exclude(payload: BatchExclude, session: Session = Depends(db_session)) -> dict:
    updated = 0
    for bid in payload.book_ids:
        book = session.get(Book, bid)
        if book is not None and book.excluded != payload.excluded:
            _apply_exclude(book, payload.excluded)
            session.add(book)
            updated += 1
    if updated:
        verb = "Excluded" if payload.excluded else "Included"
        session.add(EventLog(category="library", message=f"{verb} {updated} title(s) (bulk)"))
    session.commit()
    return {"updated": updated}


@router.post("/abs-match")
def abs_match(session: Session = Depends(db_session), rematch: bool = False) -> dict:
    """Resolve downloaded books to AudiobookShelf library items and store the item ids."""
    from app.services import audiobookshelf as abs_svc

    result = abs_svc.match_all(session, rematch=rematch)
    if not result["configured"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "AudiobookShelf URL and token are required")
    return result


@router.post("/batch/download", status_code=status.HTTP_202_ACCEPTED)
def batch_download(payload: BatchDownload, session: Session = Depends(db_session)) -> dict:
    queued = 0
    for bid in payload.book_ids:
        book = session.get(Book, bid)
        if book is None or book.excluded:
            continue
        latest = _latest_job(session, bid)
        if latest and latest.state == JobState.completed:
            continue
        try:
            if _queue_download(session, book):
                queued += 1
        except HTTPException:
            continue
    return {"queued": queued}
