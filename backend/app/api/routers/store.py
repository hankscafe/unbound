"""Audible store: catalog search + per-account wishlist (Phase 1 — no purchasing).

All calls run against the linked account's own session (marketplace follows the
account), so results, prices, and the wishlist are exactly what that user would
see in Audible's apps.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.api.deps import db_session, require_admin
from app.audible import client as ac
from app.db.models import AccountStatus, AudibleAccount, Book, EventLog
from app.schemas import StoreItemOut, StoreSearchOut, WishlistAdd
from app.services import accounts as account_svc

router = APIRouter(tags=["store"], dependencies=[Depends(require_admin)])


def _load_account(session: Session, account_id: int) -> AudibleAccount:
    account = session.get(AudibleAccount, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    if account.status != AccountStatus.linked:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Account is not linked")
    return account


def _owned_asins(session: Session, account_id: int) -> set[str]:
    return {
        b.asin
        for b in session.exec(select(Book).where(Book.audible_account_id == account_id)).all()
    }


def _to_out(item: ac.StoreItem, owned: set[str]) -> StoreItemOut:
    return StoreItemOut(
        asin=item.asin,
        title=item.title,
        subtitle=item.subtitle,
        authors=item.authors,
        narrators=item.narrators,
        series=item.series,
        series_sequence=item.series_sequence,
        runtime_minutes=item.runtime_minutes,
        cover_url=item.cover_url,
        price_display=item.price_display,
        release_date=item.release_date,
        in_library=item.asin in owned,
    )


@router.get("/search", response_model=StoreSearchOut)
def search(
    account_id: int,
    q: str = Query(min_length=1),
    page: int = 0,
    session: Session = Depends(db_session),
) -> StoreSearchOut:
    account = _load_account(session, account_id)
    try:
        auth = account_svc.load_authenticator(account)
        items, total = ac.search_catalog(auth, q, page=page)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Audible search failed: {exc}") from exc
    owned = _owned_asins(session, account_id)
    return StoreSearchOut(items=[_to_out(i, owned) for i in items], total=total, page=page)


@router.get("/wishlist", response_model=list[StoreItemOut])
def wishlist(account_id: int, session: Session = Depends(db_session)) -> list[StoreItemOut]:
    account = _load_account(session, account_id)
    try:
        auth = account_svc.load_authenticator(account)
        items = ac.get_wishlist(auth)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Wishlist fetch failed: {exc}") from exc
    owned = _owned_asins(session, account_id)
    return [_to_out(i, owned) for i in items]


@router.post("/wishlist", status_code=status.HTTP_201_CREATED)
def wishlist_add(payload: WishlistAdd, session: Session = Depends(db_session)) -> dict:
    account = _load_account(session, payload.account_id)
    try:
        auth = account_svc.load_authenticator(account)
        ac.add_to_wishlist(auth, payload.asin)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Wishlist add failed: {exc}") from exc
    session.add(EventLog(category="library", account_id=account.id,
                         message=f"Wishlisted {payload.asin} on '{account.label}'"))
    session.commit()
    return {"ok": True}


@router.delete("/wishlist/{asin}", status_code=status.HTTP_204_NO_CONTENT)
def wishlist_remove(asin: str, account_id: int, session: Session = Depends(db_session)) -> None:
    account = _load_account(session, account_id)
    try:
        auth = account_svc.load_authenticator(account)
        ac.remove_from_wishlist(auth, asin)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Wishlist remove failed: {exc}") from exc
    session.add(EventLog(category="library", account_id=account.id,
                         message=f"Removed {asin} from wishlist on '{account.label}'"))
    session.commit()
