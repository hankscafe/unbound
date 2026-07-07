"""Audible store: catalog search + per-account wishlist (Phase 1 — no purchasing).

All calls run against the linked account's own session (marketplace follows the
account), so results, prices, and the wishlist are exactly what that user would
see in Audible's apps.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from sqlmodel import col

from app.api.deps import current_user, db_session, require_account_access
from app.audible import client as ac
from app.db import init_db
from app.db.models import AccountStatus, AudibleAccount, Book, EventLog, User
from app.schemas import (
    PurchaseOut,
    PurchaseRequest,
    StoreConfigOut,
    StoreItemOut,
    StoreSearchOut,
    WishlistAdd,
)
from app.services import accounts as account_svc
from app.services import notifier

# Members may search and manage wishlists — but only on accounts they were
# granted access to (admins: all accounts).
router = APIRouter(tags=["store"])


def _load_account(session: Session, user: User, account_id: int) -> AudibleAccount:
    require_account_access(session, user, account_id)
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


@router.get("/config", response_model=StoreConfigOut)
def store_config(
    session: Session = Depends(db_session), _: User = Depends(current_user)
) -> StoreConfigOut:
    return StoreConfigOut(
        purchases_enabled=init_db.get_bool(session, init_db.SETTING_PURCHASES_ENABLED)
    )


@router.get("/search", response_model=StoreSearchOut)
def search(
    account_id: int,
    q: str = Query(min_length=1),
    page: int = 0,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> StoreSearchOut:
    account = _load_account(session, user, account_id)
    try:
        auth = account_svc.load_authenticator(account)
        items, total = ac.search_catalog(auth, q, page=page)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Audible search failed: {exc}") from exc
    owned = _owned_asins(session, account_id)
    return StoreSearchOut(items=[_to_out(i, owned) for i in items], total=total, page=page)


@router.get("/wishlist", response_model=list[StoreItemOut])
def wishlist(
    account_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[StoreItemOut]:
    account = _load_account(session, user, account_id)
    try:
        auth = account_svc.load_authenticator(account)
        items = ac.get_wishlist(auth)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Wishlist fetch failed: {exc}") from exc
    owned = _owned_asins(session, account_id)
    return [_to_out(i, owned) for i in items]


@router.post("/purchase", response_model=PurchaseOut, status_code=status.HTTP_201_CREATED)
def purchase(
    payload: PurchaseRequest,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> PurchaseOut:
    """Buy a title with ONE Audible credit. Requires the global purchasing switch
    AND the caller's personal can_spend_credits grant AND access to the account.
    Never touches the payment card — no credits means the order fails."""
    if not init_db.get_bool(session, init_db.SETTING_PURCHASES_ENABLED):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Purchasing is disabled on this server")
    if not user.can_spend_credits:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You don't have permission to spend credits"
        )
    account = _load_account(session, user, payload.account_id)
    already = session.exec(
        select(Book).where(
            Book.audible_account_id == account.id, col(Book.asin) == payload.asin
        )
    ).first()
    if already:
        raise HTTPException(status.HTTP_409_CONFLICT, "This account already owns that title")

    try:
        auth = account_svc.load_authenticator(account)
        resp = ac.purchase_with_credit(auth, payload.asin)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Purchase failed (no credit was charged unless Audible says otherwise): {exc}",
        ) from exc

    order_id = str(resp.get("order_id") or resp.get("orderId") or "") or None
    session.add(EventLog(category="store", account_id=account.id,
                         message=f"'{user.username}' bought '{payload.title}' ({payload.asin}) "
                                 f"with 1 credit on '{account.label}'"))
    session.commit()
    notifier.send_if_enabled(
        init_db.SETTING_NOTIFY_PURCHASE,
        "Unbound: audiobook purchased",
        f"{user.username} bought '{payload.title}' with 1 credit on '{account.label}'.",
        default=True,
    )
    # Pull the new title into the library and start its download automatically.
    from app.worker.queue import enqueue

    enqueue("post_purchase", account_id=account.id, asin=payload.asin)
    return PurchaseOut(
        ok=True,
        order_id=order_id,
        message="Purchased — syncing the library and queueing the download.",
    )


@router.post("/wishlist", status_code=status.HTTP_201_CREATED)
def wishlist_add(
    payload: WishlistAdd,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> dict:
    account = _load_account(session, user, payload.account_id)
    try:
        auth = account_svc.load_authenticator(account)
        ac.add_to_wishlist(auth, payload.asin)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Wishlist add failed: {exc}") from exc
    session.add(EventLog(category="library", account_id=account.id,
                         message=f"'{user.username}' wishlisted {payload.asin} on '{account.label}'"))
    session.commit()
    return {"ok": True}


@router.delete("/wishlist/{asin}", status_code=status.HTTP_204_NO_CONTENT)
def wishlist_remove(
    asin: str,
    account_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> None:
    account = _load_account(session, user, account_id)
    try:
        auth = account_svc.load_authenticator(account)
        ac.remove_from_wishlist(auth, asin)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Wishlist remove failed: {exc}") from exc
    session.add(EventLog(category="library", account_id=account.id,
                         message=f"'{user.username}' removed {asin} from wishlist on '{account.label}'"))
    session.commit()
