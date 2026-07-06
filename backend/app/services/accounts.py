"""Account service: encrypted auth-blob storage and Audible authenticator access."""

from __future__ import annotations

from app.audible import client as ac
from app.core.runtime import get_secret_box
from app.db.models import AudibleAccount


def store_auth_blob(account: AudibleAccount, blob: str) -> None:
    """Encrypt and attach a serialized authenticator to the account."""
    account.encrypted_auth_blob = get_secret_box().encrypt(blob)


def load_authenticator(account: AudibleAccount):  # -> audible.Authenticator
    """Decrypt the stored blob and reconstruct a usable authenticator."""
    if not account.encrypted_auth_blob:
        raise ValueError("Account is not linked")
    blob = get_secret_box().decrypt_str(account.encrypted_auth_blob)
    return ac.deserialize_auth(blob)


def store_activation_bytes(account: AudibleAccount, activation_bytes: str) -> None:
    account.encrypted_activation_bytes = get_secret_box().encrypt(activation_bytes)


def load_activation_bytes(account: AudibleAccount) -> str | None:
    if not account.encrypted_activation_bytes:
        return None
    return get_secret_box().decrypt_str(account.encrypted_activation_bytes)
