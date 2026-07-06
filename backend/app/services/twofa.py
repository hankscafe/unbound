"""TOTP two-factor authentication helpers (pyotp + QR generation).

The caller is responsible for encrypting/decrypting the stored secret with the
app SecretBox; functions here operate on the plaintext base32 secret.
"""

from __future__ import annotations

import base64
import hashlib
import io
import secrets

import pyotp
import qrcode

ISSUER = "Unbound"
RECOVERY_CODE_COUNT = 10


def generate_secret() -> str:
    return pyotp.random_base32()


def otpauth_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def qr_data_uri(uri: str) -> str:
    """PNG QR of the otpauth URI as a data: URI (renderable via an <img> tag)."""
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    # valid_window=1 tolerates ~30s clock skew each way.
    return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)


# --- Recovery codes --------------------------------------------------------


def generate_recovery_codes() -> list[str]:
    """Human-friendly one-time recovery codes, e.g. ``3f9a2c-8b1c74``."""
    return [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODE_COUNT)]


def _normalize(code: str) -> str:
    return code.strip().lower().replace(" ", "")


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(_normalize(code).encode("utf-8")).hexdigest()


def hash_recovery_codes(codes: list[str]) -> list[str]:
    return [hash_recovery_code(c) for c in codes]
