"""Settings: library profiles, API keys, and app preferences."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, col, select

from app.api.deps import db_session, require_admin
from app.core.security import generate_api_key
from app.db import init_db
from app.services import audiobookshelf as abs_svc
from app.services import notifier
from app.services import oidc
from app.services.storage import path_problem
from app.db.models import ApiKey, AudioFormat, EventLog, LibraryProfile
from app.schemas import (
    ApiKeyCreate,
    ApiKeyCreatedOut,
    ApiKeyOut,
    IntegrationsSettings,
    LibraryProfileIn,
    LibraryProfileOut,
    OIDCSettings,
)

router = APIRouter(tags=["settings"], dependencies=[Depends(require_admin)])


# --- Library profiles ------------------------------------------------------


def _profile_out(p: LibraryProfile) -> LibraryProfileOut:
    return LibraryProfileOut(
        id=p.id,  # type: ignore[arg-type]
        name=p.name,
        root_path=p.root_path,
        folder_template=p.folder_template,
        filename_template=p.filename_template,
        audio_format=p.audio_format.value,
        embed_cover=p.embed_cover,
        embed_chapters=p.embed_chapters,
        is_default=p.is_default,
        audible_account_id=p.audible_account_id,
    )


@router.get("/library-profiles", response_model=list[LibraryProfileOut])
def list_profiles(session: Session = Depends(db_session)) -> list[LibraryProfileOut]:
    return [_profile_out(p) for p in session.exec(select(LibraryProfile)).all()]


def _validate_root(raw: str) -> None:
    problem = path_problem(raw)
    if problem:
        raise HTTPException(422, problem)  # Unprocessable Content


@router.post("/library-profiles", response_model=LibraryProfileOut, status_code=201)
def create_profile(
    payload: LibraryProfileIn, session: Session = Depends(db_session)
) -> LibraryProfileOut:
    _validate_root(payload.root_path)
    if payload.is_default:
        for p in session.exec(select(LibraryProfile).where(LibraryProfile.is_default == True)).all():  # noqa: E712
            p.is_default = False
            session.add(p)
    profile = LibraryProfile(
        name=payload.name,
        root_path=payload.root_path,
        folder_template=payload.folder_template,
        filename_template=payload.filename_template,
        audio_format=AudioFormat(payload.audio_format),
        embed_cover=payload.embed_cover,
        embed_chapters=payload.embed_chapters,
        is_default=payload.is_default,
        audible_account_id=payload.audible_account_id,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return _profile_out(profile)


@router.put("/library-profiles/{profile_id}", response_model=LibraryProfileOut)
def update_profile(
    profile_id: int, payload: LibraryProfileIn, session: Session = Depends(db_session)
) -> LibraryProfileOut:
    profile = session.get(LibraryProfile, profile_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    _validate_root(payload.root_path)
    if payload.is_default and not profile.is_default:
        for p in session.exec(select(LibraryProfile).where(LibraryProfile.is_default == True)).all():  # noqa: E712
            p.is_default = False
            session.add(p)
    profile.name = payload.name
    profile.root_path = payload.root_path
    profile.folder_template = payload.folder_template
    profile.filename_template = payload.filename_template
    profile.audio_format = AudioFormat(payload.audio_format)
    profile.embed_cover = payload.embed_cover
    profile.embed_chapters = payload.embed_chapters
    profile.is_default = payload.is_default
    profile.audible_account_id = payload.audible_account_id
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return _profile_out(profile)


@router.delete("/library-profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: int, session: Session = Depends(db_session)) -> None:
    profile = session.get(LibraryProfile, profile_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    session.delete(profile)
    session.commit()


# --- API keys --------------------------------------------------------------


def _key_out(k: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=k.id,  # type: ignore[arg-type]
        name=k.name,
        scope=k.scope,
        is_active=k.is_active,
        created_at=k.created_at,
        last_used_at=k.last_used_at,
    )


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(session: Session = Depends(db_session)) -> list[ApiKeyOut]:
    return [_key_out(k) for k in session.exec(select(ApiKey)).all()]


@router.post("/api-keys", response_model=ApiKeyCreatedOut, status_code=201)
def create_api_key(
    payload: ApiKeyCreate, session: Session = Depends(db_session)
) -> ApiKeyCreatedOut:
    raw = generate_api_key()
    key = ApiKey(name=payload.name, key_hash=hashlib.sha256(raw.encode()).hexdigest())
    session.add(key)
    session.commit()
    session.refresh(key)
    return ApiKeyCreatedOut(**_key_out(key).model_dump(), key=raw)


@router.delete("/api-keys/{key_id}", status_code=204)
def delete_api_key(key_id: int, session: Session = Depends(db_session)) -> None:
    key = session.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    session.delete(key)
    session.commit()


# --- Preferences -----------------------------------------------------------


@router.get("/preferences")
def get_preferences(session: Session = Depends(db_session)) -> dict:
    return {
        "theme": init_db.get_setting(session, init_db.SETTING_THEME, "audible-dark"),
        "consent_acknowledged": init_db.get_setting(session, init_db.SETTING_CONSENT) == "true",
    }


@router.put("/preferences")
def update_preferences(payload: dict, session: Session = Depends(db_session)) -> dict:
    if "theme" in payload:
        init_db.set_setting(session, init_db.SETTING_THEME, str(payload["theme"]))
    session.add(EventLog(category="system", message="Preferences updated"))
    session.commit()
    return get_preferences(session)


# --- Integrations: scheduling + AudiobookShelf -----------------------------


@router.get("/integrations", response_model=IntegrationsSettings)
def get_integrations(session: Session = Depends(db_session)) -> IntegrationsSettings:
    return IntegrationsSettings(
        schedule_enabled=init_db.get_bool(session, init_db.SETTING_SCHEDULE_ENABLED),
        schedule_interval_hours=int(
            init_db.get_setting(session, init_db.SETTING_SCHEDULE_INTERVAL_HOURS, "12")
        ),
        auto_download_new=init_db.get_bool(session, init_db.SETTING_AUTO_DOWNLOAD),
        abs_url=init_db.get_setting(session, init_db.SETTING_ABS_URL),
        abs_library_id=init_db.get_setting(session, init_db.SETTING_ABS_LIBRARY_ID),
        abs_token=None,  # never echoed back
        abs_token_set=abs_svc.has_token(session),
        notify_urls="\n".join(notifier.get_urls(session)),
        notify_on_new_books=init_db.get_bool(session, init_db.SETTING_NOTIFY_NEW_BOOKS),
        notify_on_complete=init_db.get_bool(session, init_db.SETTING_NOTIFY_COMPLETE),
        notify_on_failure=init_db.get_bool(session, init_db.SETTING_NOTIFY_FAILURE, True),
    )


@router.put("/integrations", response_model=IntegrationsSettings)
def update_integrations(
    payload: IntegrationsSettings, session: Session = Depends(db_session)
) -> IntegrationsSettings:
    init_db.set_setting(session, init_db.SETTING_SCHEDULE_ENABLED, str(payload.schedule_enabled).lower())
    init_db.set_setting(
        session, init_db.SETTING_SCHEDULE_INTERVAL_HOURS, str(payload.schedule_interval_hours)
    )
    init_db.set_setting(session, init_db.SETTING_AUTO_DOWNLOAD, str(payload.auto_download_new).lower())
    init_db.set_setting(session, init_db.SETTING_ABS_URL, (payload.abs_url or "").strip() or None)
    init_db.set_setting(
        session, init_db.SETTING_ABS_LIBRARY_ID, (payload.abs_library_id or "").strip() or None
    )
    if payload.abs_token is not None and payload.abs_token.strip():
        abs_svc.set_token(session, payload.abs_token)  # only replace when a value is sent
    notifier.set_urls(session, payload.notify_urls)
    init_db.set_setting(session, init_db.SETTING_NOTIFY_NEW_BOOKS, str(payload.notify_on_new_books).lower())
    init_db.set_setting(session, init_db.SETTING_NOTIFY_COMPLETE, str(payload.notify_on_complete).lower())
    init_db.set_setting(session, init_db.SETTING_NOTIFY_FAILURE, str(payload.notify_on_failure).lower())
    session.add(EventLog(category="system", message="Integrations settings updated"))
    session.commit()
    return get_integrations(session)


@router.post("/integrations/test-audiobookshelf")
def test_audiobookshelf(session: Session = Depends(db_session)) -> dict:
    """Verify the ABS URL + token by listing libraries."""
    base, token, _ = abs_svc.get_config(session)
    if not base or not token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "AudiobookShelf URL and token are required")
    try:
        return abs_svc.test_connection(base, token)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not reach AudiobookShelf: {exc}")


@router.post("/integrations/test-notification")
def test_notification(session: Session = Depends(db_session)) -> dict:
    """Send a test notification to the configured targets."""
    urls = notifier.get_urls(session)
    if not urls:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No notification URLs configured")
    notifier.send("Unbound: test notification", "If you can read this, notifications work. 🎧")
    return {"sent_to": len(urls)}


# --- OIDC single sign-on -----------------------------------------------------


def _oidc_out(session: Session) -> OIDCSettings:
    base = init_db.get_setting(session, oidc.SETTING_OIDC_PUBLIC_BASE_URL)
    return OIDCSettings(
        enabled=init_db.get_bool(session, oidc.SETTING_OIDC_ENABLED),
        issuer=init_db.get_setting(session, oidc.SETTING_OIDC_ISSUER),
        client_id=init_db.get_setting(session, oidc.SETTING_OIDC_CLIENT_ID),
        client_secret=None,  # never echoed back
        client_secret_set=oidc.has_client_secret(session),
        button_label=init_db.get_setting(session, oidc.SETTING_OIDC_BUTTON_LABEL),
        public_base_url=base,
        redirect_uri=f"{base.rstrip('/')}/api/auth/oidc/callback" if base else None,
    )


@router.get("/oidc", response_model=OIDCSettings)
def get_oidc(session: Session = Depends(db_session)) -> OIDCSettings:
    return _oidc_out(session)


@router.put("/oidc", response_model=OIDCSettings)
def update_oidc(payload: OIDCSettings, session: Session = Depends(db_session)) -> OIDCSettings:
    if payload.enabled:
        missing = not (payload.issuer or "").strip() or not (payload.client_id or "").strip()
        no_secret = not (payload.client_secret or "").strip() and not oidc.has_client_secret(session)
        if missing or no_secret:
            raise HTTPException(422, "Enabling OIDC requires issuer, client ID, and client secret")
    init_db.set_setting(session, oidc.SETTING_OIDC_ENABLED, str(payload.enabled).lower())
    init_db.set_setting(
        session, oidc.SETTING_OIDC_ISSUER, (payload.issuer or "").strip().rstrip("/") or None
    )
    init_db.set_setting(session, oidc.SETTING_OIDC_CLIENT_ID, (payload.client_id or "").strip() or None)
    if payload.client_secret is not None and payload.client_secret.strip():
        oidc.set_client_secret(session, payload.client_secret.strip())  # only replace when sent
    init_db.set_setting(
        session, oidc.SETTING_OIDC_BUTTON_LABEL, (payload.button_label or "").strip() or None
    )
    init_db.set_setting(
        session, oidc.SETTING_OIDC_PUBLIC_BASE_URL, (payload.public_base_url or "").strip().rstrip("/") or None
    )
    session.add(EventLog(category="system", message="OIDC settings updated"))
    session.commit()
    return _oidc_out(session)


@router.post("/oidc/test")
def test_oidc(session: Session = Depends(db_session)) -> dict:
    """Verify the issuer by fetching its discovery document."""
    issuer = init_db.get_setting(session, oidc.SETTING_OIDC_ISSUER)
    if not issuer:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Set the issuer URL first")
    try:
        doc = oidc.discover(issuer)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Discovery failed: {exc}")
    return {"ok": True, "authorization_endpoint": doc["authorization_endpoint"]}


# --- Activity feed ---------------------------------------------------------


@router.get("/events")
def recent_events(session: Session = Depends(db_session), limit: int = 50) -> list[dict]:
    rows = session.exec(select(EventLog).order_by(col(EventLog.id).desc()).limit(limit)).all()
    return [
        {
            "id": e.id,
            "ts": e.ts.isoformat(),
            "level": e.level,
            "category": e.category,
            "message": e.message,
        }
        for e in rows
    ]
