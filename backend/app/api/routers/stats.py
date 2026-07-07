"""Stats + health endpoints. Consumable by a session OR a read-only API key."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, func, select

from app.api.deps import api_key_or_session, db_session
from app.db.models import Book
from app.schemas import StatsOut
from app.services import network as network_svc
from app.services import stats as stats_svc
from app.services import storage as storage_svc
from app.services import updates as updates_svc

router = APIRouter(tags=["stats"])


@router.get("/health")
def health() -> dict:
    """Unauthenticated liveness probe."""
    return {"status": "ok"}


@router.get("/covers")
def covers(session: Session = Depends(db_session), limit: int = Query(30, le=60)) -> dict:
    """Random book cover URLs for the login page background. Unauthenticated —
    returns only public Audible cover-image URLs (no titles or account data)."""
    rows = session.exec(
        select(Book.cover_url)
        .where(Book.cover_url.is_not(None))  # type: ignore[union-attr]
        .order_by(func.random())
        .limit(limit)
    ).all()
    return {"covers": [r for r in rows if r]}


@router.get("/stats", response_model=StatsOut, dependencies=[Depends(api_key_or_session)])
def get_stats(session: Session = Depends(db_session)) -> StatsOut:
    data = stats_svc.gather_stats(session)
    upd = updates_svc.check_for_update()
    storage = storage_svc.compute_storage(session)
    return StatsOut(**data, **upd, **storage, network_online=network_svc.is_online())
