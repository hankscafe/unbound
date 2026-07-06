"""Security primitives: secret encryption at rest, password hashing, session tokens.

- **Encryption at rest** uses AES-256-GCM (authenticated) via a key derived from the
  master secret with HKDF. Ciphertext is versioned so the scheme can evolve and keys
  can be rotated. Used for Audible device tokens/keys and any stored secret material.
- **Passwords** use Argon2id (argon2-cffi).
- **Sessions** are signed JWTs carried in an HttpOnly cookie.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_ph = PasswordHasher()

# --- Passwords -------------------------------------------------------------


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def needs_rehash(password_hash: str) -> bool:
    return _ph.check_needs_rehash(password_hash)


# --- Secret encryption at rest --------------------------------------------

_SCHEME_VERSION = b"\x01"
_INFO = b"unbound-secret-encryption-v1"


class SecretBox:
    """Authenticated encryption for secrets at rest.

    Derives a 32-byte AES-GCM key from the master secret via HKDF-SHA256, so the
    stored key material is decoupled from the raw env value. The 8-byte truncated
    key fingerprint lets the app detect a changed master key (rotation) without
    ever logging the key itself.
    """

    def __init__(self, master_secret: str) -> None:
        self._key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO
        ).derive(master_secret.encode("utf-8"))
        self._aead = AESGCM(self._key)

    @property
    def fingerprint(self) -> str:
        digest = hashes.Hash(hashes.SHA256())
        digest.update(self._key)
        return digest.finalize()[:8].hex()

    def encrypt(self, plaintext: str | bytes) -> str:
        if isinstance(plaintext, str):
            plaintext = plaintext.encode("utf-8")
        nonce = os.urandom(12)
        ct = self._aead.encrypt(nonce, plaintext, None)
        return base64.urlsafe_b64encode(_SCHEME_VERSION + nonce + ct).decode("ascii")

    def decrypt(self, token: str) -> bytes:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        if raw[:1] != _SCHEME_VERSION:
            raise ValueError("Unsupported secret encryption scheme version")
        nonce, ct = raw[1:13], raw[13:]
        return self._aead.decrypt(nonce, ct, None)

    def decrypt_str(self, token: str) -> str:
        return self.decrypt(token).decode("utf-8")


# --- Session tokens (JWT) --------------------------------------------------

_ALG = "HS256"


def create_session_token(*, subject: str, session_secret: str, ttl_seconds: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "typ": "session",
    }
    return jwt.encode(payload, session_secret, algorithm=_ALG)


def decode_session_token(token: str, *, session_secret: str) -> dict:
    return jwt.decode(token, session_secret, algorithms=[_ALG])


def generate_api_key() -> str:
    """A read-only API key for widget consumers (Homepage). Prefixed for clarity."""
    return "unb_" + base64.urlsafe_b64encode(os.urandom(24)).decode("ascii").rstrip("=")
