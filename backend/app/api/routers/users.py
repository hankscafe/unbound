"""Admin user management: members, roles, purchase grant, account access."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.api.deps import db_session, require_admin
from app.core.security import hash_password
from app.db.models import AudibleAccount, EventLog, User, UserAccountAccess, UserRole
from app.schemas import UserAdminCreate, UserAdminOut, UserAdminUpdate

router = APIRouter(tags=["users"])


def _account_ids(session: Session, user_id: int) -> list[int]:
    rows = session.exec(
        select(UserAccountAccess).where(UserAccountAccess.user_id == user_id)
    ).all()
    return sorted(r.audible_account_id for r in rows)


def _out(session: Session, user: User) -> UserAdminOut:
    return UserAdminOut(
        id=user.id,  # type: ignore[arg-type]
        username=user.username,
        email=user.email,
        role=user.role.value,
        is_active=user.is_active,
        totp_enabled=user.totp_enabled,
        can_spend_credits=user.can_spend_credits,
        account_ids=_account_ids(session, user.id),  # type: ignore[arg-type]
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _other_active_admins(session: Session, user: User) -> int:
    rows = session.exec(
        select(User).where(User.role == UserRole.admin, User.is_active == True)  # noqa: E712
    ).all()
    return len([u for u in rows if u.id != user.id])


def _set_account_access(session: Session, user: User, account_ids: list[int]) -> None:
    valid = {a.id for a in session.exec(select(AudibleAccount)).all()}
    unknown = set(account_ids) - valid
    if unknown:
        raise HTTPException(422, f"Unknown account id(s): {sorted(unknown)}")
    for row in session.exec(
        select(UserAccountAccess).where(UserAccountAccess.user_id == user.id)
    ).all():
        session.delete(row)
    for aid in set(account_ids):
        session.add(UserAccountAccess(user_id=user.id, audible_account_id=aid))  # type: ignore[arg-type]


@router.get("", response_model=list[UserAdminOut])
def list_users(
    session: Session = Depends(db_session), _: User = Depends(require_admin)
) -> list[UserAdminOut]:
    return [_out(session, u) for u in session.exec(select(User).order_by(User.id)).all()]


@router.post("", response_model=UserAdminOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserAdminCreate,
    session: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> UserAdminOut:
    if session.exec(select(User).where(User.username == payload.username)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=UserRole(payload.role),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    if payload.account_ids:
        _set_account_access(session, user, payload.account_ids)
    session.add(EventLog(category="auth",
                         message=f"User '{user.username}' ({user.role.value}) created by '{admin.username}'"))
    session.commit()
    return _out(session, user)


@router.put("/{user_id}", response_model=UserAdminOut)
def update_user(
    user_id: int,
    payload: UserAdminUpdate,
    session: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> UserAdminOut:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    demoting = payload.role is not None and payload.role != UserRole.admin.value
    deactivating = payload.is_active is False
    if user.role == UserRole.admin and (demoting or deactivating):
        if _other_active_admins(session, user) == 0:
            raise HTTPException(422, "At least one active admin must remain")

    if payload.email is not None:
        user.email = payload.email.strip() or None
    if payload.role is not None:
        user.role = UserRole(payload.role)
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.can_spend_credits is not None:
        user.can_spend_credits = payload.can_spend_credits
    if payload.password:
        user.password_hash = hash_password(payload.password)
    if payload.account_ids is not None:
        _set_account_access(session, user, payload.account_ids)
    session.add(user)
    session.add(EventLog(category="auth",
                         message=f"User '{user.username}' updated by '{admin.username}'"))
    session.commit()
    session.refresh(user)
    return _out(session, user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    session: Session = Depends(db_session),
    admin: User = Depends(require_admin),
) -> None:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id == admin.id:
        raise HTTPException(422, "You cannot delete your own account")
    if user.role == UserRole.admin and _other_active_admins(session, user) == 0:
        raise HTTPException(422, "At least one active admin must remain")
    for row in session.exec(
        select(UserAccountAccess).where(UserAccountAccess.user_id == user.id)
    ).all():
        session.delete(row)
    session.add(EventLog(category="auth",
                         message=f"User '{user.username}' deleted by '{admin.username}'"))
    session.delete(user)
    session.commit()
