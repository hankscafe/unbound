"""Stats + health endpoints. Consumable by a session OR a read-only API key."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import api_key_or_session, db_session
from app.schemas import StatsOut
from app.services import stats as stats_svc
from app.services import storage as storage_svc
from app.services import updates as updates_svc

router = APIRouter(tags=["stats"])


@router.get("/health")
def health() -> dict:
    """Unauthenticated liveness probe."""
    return {"status": "ok"}


@router.get("/stats", response_model=StatsOut, dependencies=[Depends(api_key_or_session)])
def get_stats(session: Session = Depends(db_session)) -> StatsOut:
    data = stats_svc.gather_stats(session)
    upd = updates_svc.check_for_update()
    storage = storage_svc.compute_storage(session)
    return StatsOut(**data, **upd, **storage)
