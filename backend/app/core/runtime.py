"""Process-wide runtime singletons (secret box, settings)."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.core.security import SecretBox


@lru_cache
def get_secret_box() -> SecretBox:
    """The app's authenticated-encryption box, keyed off the master secret."""
    return SecretBox(get_settings().resolve_secret_key())
