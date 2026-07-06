"""Authentication & first-run setup endpoints."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.api.deps import current_user, db_session
from app.core.config import get_settings
import json

from app.core.runtime import get_secret_box
from app.core.security import (
    create_mfa_token,
    create_session_token,
    decode_mfa_token,
    needs_rehash,
    hash_password,
    verify_password,
)
from app.db import init_db
from app.db.models import EventLog, User
from app.schemas import (
    Login2FARequest,
    LoginRequest,
    LoginResult,
    SetupRequest,
    StatusOut,
    TwoFACodeRequest,
    TwoFAEnableOut,
    TwoFASetupOut,
    UserOut,
)
from app.services import oidc, twofa

router = APIRouter(tags=["auth"])
settings = get_settings()

# --- Simple in-memory login rate limiting (per client IP) ---
_LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_MAX_ATTEMPTS = 8
_WINDOW_SECONDS = 300  # 5 minutes


def _rate_limit_login(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    recent = [t for t in _LOGIN_ATTEMPTS[ip] if now - t < _WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[ip] = recent
    if len(recent) >= _MAX_ATTEMPTS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many login attempts. Try again in a few minutes.",
        )


def _record_login_attempt(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    _LOGIN_ATTEMPTS[ip].append(time.time())


def _set_session_cookie(response: Response, user_id: int) -> None:
    token = create_session_token(
        subject=str(user_id),
        session_secret=settings.resolve_session_secret(),
        ttl_seconds=settings.session_ttl_seconds,
    )
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,  # type: ignore[arg-type]
        username=user.username,
        email=user.email,
        role=user.role.value,
        totp_enabled=user.totp_enabled,
    )


def _finalize_login(user: User, request: Request, response: Response, session: Session) -> UserOut:
    user.last_login_at = datetime.now(timezone.utc)
    session.add(user)
    session.add(EventLog(category="auth", message=f"User '{user.username}' logged in"))
    session.commit()
    _set_session_cookie(response, user.id)  # type: ignore[arg-type]
    return _user_out(user)


@router.get("/status", response_model=StatusOut)
def get_status(request: Request, session: Session = Depends(db_session)) -> StatusOut:
    from app import __version__

    authed = False
    token = request.cookies.get(settings.cookie_name)
    if token:
        try:
            from app.core.security import decode_session_token

            decode_session_token(token, session_secret=settings.resolve_session_secret())
            authed = True
        except Exception:
            authed = False
    oidc_on = oidc.is_enabled(session)
    return StatusOut(
        setup_required=init_db.setup_required(session),
        authenticated=authed,
        consent_acknowledged=init_db.get_setting(session, init_db.SETTING_CONSENT) == "true",
        secret_key_rotated=init_db.secret_key_rotated(session),
        version=__version__,
        oidc_enabled=oidc_on,
        oidc_button_label=oidc.button_label(session) if oidc_on else None,
    )


@router.post("/setup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def setup(
    payload: SetupRequest, response: Response, session: Session = Depends(db_session)
) -> UserOut:
    if not init_db.setup_required(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup already completed")
    if not payload.consent:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "You must acknowledge the personal-use terms to continue",
        )
    user = init_db.create_admin(
        session, username=payload.username, password=payload.password, email=payload.email
    )
    init_db.set_setting(session, init_db.SETTING_CONSENT, "true")
    init_db.seed_defaults(session)
    session.add(EventLog(category="auth", message=f"Admin account '{user.username}' created"))
    session.commit()
    _set_session_cookie(response, user.id)  # type: ignore[arg-type]
    return _user_out(user)


@router.post("/login", response_model=LoginResult)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(db_session),
) -> LoginResult:
    _rate_limit_login(request)
    user = session.exec(select(User).where(User.username == payload.username)).first()
    # Constant-ish work whether or not the user exists.
    if user is None or not verify_password(payload.password, user.password_hash):
        _record_login_attempt(request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        session.add(user)
        session.commit()

    if user.totp_enabled:
        # Password OK, but a 2FA code is still required — issue a short-lived token.
        token = create_mfa_token(
            subject=str(user.id), session_secret=settings.resolve_session_secret()
        )
        return LoginResult(mfa_required=True, mfa_token=token)

    return LoginResult(user=_finalize_login(user, request, response, session))


@router.post("/login/2fa", response_model=LoginResult)
def login_2fa(
    payload: Login2FARequest,
    request: Request,
    response: Response,
    session: Session = Depends(db_session),
) -> LoginResult:
    _rate_limit_login(request)
    try:
        claims = decode_mfa_token(payload.mfa_token, session_secret=settings.resolve_session_secret())
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "2FA session expired — sign in again") from exc
    user = session.get(User, int(claims["sub"]))
    if user is None or not user.is_active or not user.totp_enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid 2FA session")

    if not _verify_second_factor(session, user, payload.code):
        _record_login_attempt(request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid authentication code")

    return LoginResult(user=_finalize_login(user, request, response, session))


def _verify_second_factor(session: Session, user: User, code: str) -> bool:
    """Verify a TOTP code, or consume a one-time recovery code."""
    secret = get_secret_box().decrypt_str(user.totp_secret) if user.totp_secret else None
    if secret and twofa.verify_totp(secret, code):
        return True
    # Fall back to recovery codes.
    hashes = json.loads(user.totp_recovery_codes) if user.totp_recovery_codes else []
    h = twofa.hash_recovery_code(code)
    if h in hashes:
        hashes.remove(h)
        user.totp_recovery_codes = json.dumps(hashes)
        session.add(user)
        session.add(EventLog(level="warning", category="auth",
                             message=f"Recovery code used for '{user.username}' ({len(hashes)} left)"))
        session.commit()
        return True
    return False


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    response.delete_cookie(settings.cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return _user_out(user)


# --- OIDC single sign-on -----------------------------------------------------


def _oidc_redirect_uri(request: Request, config: oidc.OIDCConfig) -> str:
    """The callback URL registered at the IdP. Prefer the configured public base
    URL — behind the nginx proxy the request base points at the backend host."""
    base = (config.public_base_url or str(request.base_url)).rstrip("/")
    return f"{base}/api/auth/oidc/callback"


@router.get("/oidc/login")
def oidc_login(request: Request, session: Session = Depends(db_session)) -> RedirectResponse:
    config = oidc.get_config(session)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "OIDC sign-on is not configured")
    try:
        url = oidc.start_flow(config, _oidc_redirect_uri(request, config))
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Could not reach the identity provider: {exc}"
        ) from exc
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/oidc/callback")
def oidc_callback(
    request: Request,
    session: Session = Depends(db_session),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    """IdP redirect target. On success sets the session cookie and lands on the app;
    on failure lands on the login screen with an error message in the query string."""

    def fail(message: str) -> RedirectResponse:
        from urllib.parse import quote

        return RedirectResponse(f"/?oidc_error={quote(message[:200])}")

    config = oidc.get_config(session)
    if config is None:
        return fail("OIDC sign-on is not configured")
    if error:
        return fail(error_description or error)
    if not code or not state:
        return fail("The identity provider response was incomplete")
    _rate_limit_login(request)
    try:
        claims = oidc.complete_flow(config, state, code, _oidc_redirect_uri(request, config))
    except Exception as exc:
        _record_login_attempt(request)
        return fail(str(exc))
    user = oidc.match_user(session, claims)
    if user is None:
        _record_login_attempt(request)
        ident = claims.get("email") or claims.get("preferred_username") or claims.get("sub", "?")
        session.add(EventLog(level="warning", category="auth",
                             message=f"OIDC sign-in rejected: no local user matches '{ident}'"))
        session.commit()
        return fail("No Unbound user matches this identity")

    # The IdP is the authority for MFA, so a local TOTP challenge is skipped here.
    user.last_login_at = datetime.now(timezone.utc)
    session.add(user)
    session.add(EventLog(category="auth", message=f"User '{user.username}' logged in via OIDC"))
    session.commit()
    response = RedirectResponse("/")
    _set_session_cookie(response, user.id)  # type: ignore[arg-type]
    return response


# --- Two-factor authentication (TOTP) --------------------------------------


@router.post("/2fa/setup", response_model=TwoFASetupOut)
def twofa_setup(
    user: User = Depends(current_user), session: Session = Depends(db_session)
) -> TwoFASetupOut:
    """Begin enrollment: generate a secret (pending until verified) + QR."""
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "2FA is already enabled")
    secret = twofa.generate_secret()
    user.totp_secret = get_secret_box().encrypt(secret)  # stored pending, not yet enabled
    session.add(user)
    session.commit()
    uri = twofa.otpauth_uri(secret, user.username)
    return TwoFASetupOut(secret=secret, otpauth_uri=uri, qr=twofa.qr_data_uri(uri))


@router.post("/2fa/enable", response_model=TwoFAEnableOut)
def twofa_enable(
    payload: TwoFACodeRequest,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> TwoFAEnableOut:
    """Confirm the code from the authenticator app, then activate 2FA."""
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "2FA is already enabled")
    if not user.totp_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Start setup first")
    secret = get_secret_box().decrypt_str(user.totp_secret)
    if not twofa.verify_totp(secret, payload.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid authentication code")
    codes = twofa.generate_recovery_codes()
    user.totp_recovery_codes = json.dumps(twofa.hash_recovery_codes(codes))
    user.totp_enabled = True
    session.add(user)
    session.add(EventLog(category="auth", message=f"2FA enabled for '{user.username}'"))
    session.commit()
    return TwoFAEnableOut(recovery_codes=codes)


@router.post("/2fa/disable", status_code=status.HTTP_204_NO_CONTENT)
def twofa_disable(
    payload: TwoFACodeRequest,
    response: Response,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> Response:
    """Turn off 2FA after verifying a current code (or recovery code)."""
    if not user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA is not enabled")
    if not _verify_second_factor(session, user, payload.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid authentication code")
    user.totp_enabled = False
    user.totp_secret = None
    user.totp_recovery_codes = None
    session.add(user)
    session.add(EventLog(category="auth", message=f"2FA disabled for '{user.username}'"))
    session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
