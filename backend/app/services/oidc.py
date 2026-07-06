"""OpenID Connect (OIDC) single sign-on.

Generic authorization-code + PKCE flow against any spec-compliant provider
(Authentik, Keycloak, Authelia, Google, ...). Configuration lives in app
settings (client secret encrypted at rest). An OIDC identity signs in as an
*existing* local user matched by email or username — no auto-provisioning,
so a stray account at the IdP can't mint itself admin access.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass

import httpx
import jwt
from sqlmodel import Session, select

from app.core.logging import get_logger
from app.core.runtime import get_secret_box
from app.db import init_db
from app.db.models import User

log = get_logger("services.oidc")

# Setting keys (registered here; init_db holds only the shared helpers).
SETTING_OIDC_ENABLED = "oidc_enabled"
SETTING_OIDC_ISSUER = "oidc_issuer"
SETTING_OIDC_CLIENT_ID = "oidc_client_id"
SETTING_OIDC_CLIENT_SECRET = "oidc_client_secret_enc"  # encrypted at rest
SETTING_OIDC_BUTTON_LABEL = "oidc_button_label"
SETTING_OIDC_PUBLIC_BASE_URL = "oidc_public_base_url"

_FLOW_TTL_SECONDS = 600
_DISCOVERY_TTL_SECONDS = 600


@dataclass
class OIDCConfig:
    issuer: str
    client_id: str
    client_secret: str
    button_label: str
    public_base_url: str | None


def is_enabled(session: Session) -> bool:
    return init_db.get_bool(session, SETTING_OIDC_ENABLED)


def button_label(session: Session) -> str:
    return init_db.get_setting(session, SETTING_OIDC_BUTTON_LABEL) or "Single sign-on"


def get_config(session: Session) -> OIDCConfig | None:
    """The active config, or None when disabled/incomplete."""
    if not is_enabled(session):
        return None
    issuer = init_db.get_setting(session, SETTING_OIDC_ISSUER)
    client_id = init_db.get_setting(session, SETTING_OIDC_CLIENT_ID)
    secret_enc = init_db.get_setting(session, SETTING_OIDC_CLIENT_SECRET)
    if not issuer or not client_id or not secret_enc:
        return None
    return OIDCConfig(
        issuer=issuer.rstrip("/"),
        client_id=client_id,
        client_secret=get_secret_box().decrypt_str(secret_enc),
        button_label=button_label(session),
        public_base_url=init_db.get_setting(session, SETTING_OIDC_PUBLIC_BASE_URL),
    )


def set_client_secret(session: Session, secret: str) -> None:
    init_db.set_setting(session, SETTING_OIDC_CLIENT_SECRET, get_secret_box().encrypt(secret))


def has_client_secret(session: Session) -> bool:
    return init_db.get_setting(session, SETTING_OIDC_CLIENT_SECRET) is not None


# --- Provider discovery ------------------------------------------------------

_discovery_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def discover(issuer: str) -> dict:
    """Fetch (and briefly cache) the issuer's openid-configuration document."""
    issuer = issuer.rstrip("/")
    with _lock:
        cached = _discovery_cache.get(issuer)
        if cached and time.time() - cached[0] < _DISCOVERY_TTL_SECONDS:
            return cached[1]
    url = f"{issuer}/.well-known/openid-configuration"
    resp = httpx.get(url, timeout=10.0, follow_redirects=True)
    resp.raise_for_status()
    doc = resp.json()
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if key not in doc:
            raise ValueError(f"OIDC discovery document missing '{key}'")
    with _lock:
        _discovery_cache[issuer] = (time.time(), doc)
    return doc


# --- Login flows (state kept server-side, like account-linking flows) --------

_flows: dict[str, dict] = {}


def _prune_flows() -> None:
    now = time.time()
    for state in [s for s, f in _flows.items() if now - f["ts"] > _FLOW_TTL_SECONDS]:
        _flows.pop(state, None)


def start_flow(config: OIDCConfig, redirect_uri: str) -> str:
    """Create a login flow and return the provider authorization URL."""
    _prune_flows()
    doc = discover(config.issuer)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    _flows[state] = {"ts": time.time(), "nonce": nonce, "verifier": verifier}
    params = httpx.QueryParams(
        response_type="code",
        client_id=config.client_id,
        redirect_uri=redirect_uri,
        scope="openid profile email",
        state=state,
        nonce=nonce,
        code_challenge=challenge,
        code_challenge_method="S256",
    )
    return f"{doc['authorization_endpoint']}?{params}"


def _exchange_code(config: OIDCConfig, doc: dict, code: str, verifier: str, redirect_uri: str) -> dict:
    """Trade the authorization code for tokens."""
    resp = httpx.post(
        doc["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "code_verifier": verifier,
        },
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


def _validate_id_token(config: OIDCConfig, doc: dict, id_token: str, nonce: str) -> dict:
    """Verify the ID token signature (JWKS) and standard claims; return claims."""
    signing_key = jwt.PyJWKClient(doc["jwks_uri"]).get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256", "ES256", "PS256"],
        audience=config.client_id,
        issuer=config.issuer,
    )
    if claims.get("nonce") != nonce:
        raise ValueError("OIDC nonce mismatch")
    return claims


def complete_flow(config: OIDCConfig, state: str, code: str, redirect_uri: str) -> dict:
    """Validate the callback and return the verified ID-token claims."""
    _prune_flows()
    flow = _flows.pop(state, None)
    if flow is None:
        raise ValueError("Unknown or expired OIDC login attempt — try again")
    doc = discover(config.issuer)
    tokens = _exchange_code(config, doc, code, flow["verifier"], redirect_uri)
    id_token = tokens.get("id_token")
    if not id_token:
        raise ValueError("Provider response did not include an id_token")
    return _validate_id_token(config, doc, id_token, flow["nonce"])


def match_user(session: Session, claims: dict) -> User | None:
    """Map verified claims onto an existing, active local user.

    Match order: email (case-insensitive), then preferred_username against the
    local username. Unmatched identities are rejected — no auto-provisioning.
    """
    email = (claims.get("email") or "").strip().lower()
    username = (claims.get("preferred_username") or "").strip()
    for user in session.exec(select(User)).all():
        if not user.is_active:
            continue
        if email and (user.email or "").strip().lower() == email:
            return user
        if username and user.username == username:
            return user
    return None
