"""Audible account management: CRUD + guided/external linking + sync trigger."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.api.deps import db_session, require_admin
from app.audible import linking
from app.audible.client import AudibleUnavailable
from app.db.models import AccountStatus, AudibleAccount, EventLog
from app.schemas import (
    AccountCreate,
    AccountOut,
    ExternalLinkComplete,
    GuidedLinkStart,
    LinkCaptcha,
    LinkOtp,
    LinkStepOut,
)
from app.services import accounts as account_svc
from app.worker.queue import enqueue

router = APIRouter(tags=["accounts"], dependencies=[Depends(require_admin)])


def _to_out(a: AudibleAccount) -> AccountOut:
    return AccountOut(
        id=a.id,  # type: ignore[arg-type]
        label=a.label,
        marketplace=a.marketplace,
        account_owner_email=a.account_owner_email,
        status=a.status.value,
        badge_color=a.badge_color,
        last_sync_at=a.last_sync_at,
        last_error=a.last_error,
    )


@router.get("", response_model=list[AccountOut])
def list_accounts(session: Session = Depends(db_session)) -> list[AccountOut]:
    rows = session.exec(select(AudibleAccount).order_by(AudibleAccount.id)).all()
    return [_to_out(a) for a in rows]


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, session: Session = Depends(db_session)) -> AccountOut:
    account = AudibleAccount(
        label=payload.label, marketplace=payload.marketplace, badge_color=payload.badge_color
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return _to_out(account)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(account_id: int, session: Session = Depends(db_session)) -> None:
    account = session.get(AudibleAccount, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    session.delete(account)
    session.commit()


def _get_account(session: Session, account_id: int) -> AudibleAccount:
    account = session.get(AudibleAccount, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    return account


def _step_to_out(step: linking.Step) -> LinkStepOut:
    return LinkStepOut(
        status=step.status,
        flow_id=step.data.get("flow_id"),
        captcha_image_url=step.data.get("captcha_image_url"),
        message=step.data.get("message"),
    )


def _persist_if_linked(
    session: Session, account: AudibleAccount, step: linking.Step
) -> LinkStepOut:
    """When a flow reaches LINKED, pull the auth blob, encrypt, and store it."""
    if step.status != linking.LINKED:
        if step.status == linking.ERROR:
            account.status = AccountStatus.error
            account.last_error = step.data.get("message")
            session.add(account)
            session.commit()
        return _step_to_out(step)
    flow_id = step.data.get("flow_id")
    blob = linking.manager.take_result(flow_id) if flow_id else None
    if not blob:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Link completed without auth data")
    account_svc.store_auth_blob(account, blob)
    account.status = AccountStatus.linked
    account.account_owner_email = step.data.get("account_owner_email") or account.account_owner_email
    account.last_error = None
    session.add(account)
    session.add(EventLog(category="account", account_id=account.id, message=f"Linked '{account.label}'"))
    session.commit()
    # Kick off an initial library sync.
    enqueue("sync_account", account_id=account.id)
    return _step_to_out(step)


@router.post("/{account_id}/link/guided", response_model=LinkStepOut)
def link_guided(
    account_id: int, payload: GuidedLinkStart, session: Session = Depends(db_session)
) -> LinkStepOut:
    account = _get_account(session, account_id)
    try:
        flow_id, step = linking.manager.start_guided(
            email=payload.email, password=payload.password, marketplace=account.marketplace
        )
    except AudibleUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    account.status = AccountStatus.linking
    session.add(account)
    session.commit()
    return _persist_if_linked(session, account, step)


@router.post("/{account_id}/link/otp", response_model=LinkStepOut)
def link_otp(
    account_id: int, payload: LinkOtp, flow_id: str, session: Session = Depends(db_session)
) -> LinkStepOut:
    account = _get_account(session, account_id)
    step = linking.manager.provide(flow_id, "otp", payload.otp)
    return _persist_if_linked(session, account, step)


@router.post("/{account_id}/link/captcha", response_model=LinkStepOut)
def link_captcha(
    account_id: int, payload: LinkCaptcha, flow_id: str, session: Session = Depends(db_session)
) -> LinkStepOut:
    account = _get_account(session, account_id)
    step = linking.manager.provide(flow_id, "captcha", payload.captcha_answer)
    return _persist_if_linked(session, account, step)


@router.post("/{account_id}/link/external/start", response_model=LinkStepOut)
def link_external_start(account_id: int, session: Session = Depends(db_session)) -> LinkStepOut:
    account = _get_account(session, account_id)
    try:
        flow_id, step = linking.manager.start_external(marketplace=account.marketplace)
    except AudibleUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    account.status = AccountStatus.linking
    session.add(account)
    session.commit()
    out = _step_to_out(step)
    # For external flow the first step carries the login URL in step.data.
    out.message = step.data.get("login_url") or out.message
    return out


@router.post("/{account_id}/link/external/complete", response_model=LinkStepOut)
def link_external_complete(
    account_id: int, payload: ExternalLinkComplete, session: Session = Depends(db_session)
) -> LinkStepOut:
    account = _get_account(session, account_id)
    step = linking.manager.provide(payload.flow_id, "response_url", payload.response_url)
    return _persist_if_linked(session, account, step)


@router.post("/{account_id}/unlink", response_model=AccountOut)
def unlink(account_id: int, session: Session = Depends(db_session)) -> AccountOut:
    account = _get_account(session, account_id)
    account.encrypted_auth_blob = None
    account.encrypted_activation_bytes = None
    account.status = AccountStatus.unlinked
    session.add(account)
    session.add(EventLog(category="account", account_id=account.id, message=f"Unlinked '{account.label}'"))
    session.commit()
    return _to_out(account)


@router.post("/{account_id}/sync", response_model=AccountOut)
def sync_account(account_id: int, session: Session = Depends(db_session)) -> AccountOut:
    account = _get_account(session, account_id)
    if account.status != AccountStatus.linked:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Account is not linked")
    enqueue("sync_account", account_id=account_id)
    session.add(EventLog(category="account", account_id=account_id, message="Sync queued"))
    session.commit()
    return _to_out(account)
