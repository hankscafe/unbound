"""Structured logging with secret redaction.

Every log record passes through :func:`redact_processor`, which scrubs known
sensitive keys and token-like values so credentials, device keys, vouchers,
and cookies never reach the logs.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import structlog

# Keys whose values must always be masked, regardless of nesting.
SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "secret_key",
    "session_secret",
    "token",
    "access_token",
    "refresh_token",
    "auth_blob",
    "encrypted_auth_blob",
    "device_private_key",
    "adp_token",
    "activation_bytes",
    "key",
    "iv",
    "voucher",
    "cookie",
    "authorization",
    "api_key",
    "otp",
    "captcha_answer",
}

_MASK = "***REDACTED***"
# Long hex/base64-ish blobs that look like keys or tokens.
_TOKENISH = re.compile(r"^[A-Za-z0-9+/=_-]{24,}$")


def _redact_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _redact_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(key, v) for v in value)
    if key.lower() in SENSITIVE_KEYS:
        return _MASK
    if isinstance(value, str) and _TOKENISH.match(value) and key.lower() not in {"event", "logger"}:
        return _MASK
    return value


def redact_processor(_logger: Any, _method: str, event_dict: dict) -> dict:
    return {k: _redact_value(k, v) for k, v in event_dict.items()}


def configure_logging(
    *,
    level: str = "INFO",
    json_logs: bool = True,
    log_dir: Path | None = None,
) -> None:
    """Configure stdlib + structlog. Emits to stdout (Docker-friendly) and,
    if ``log_dir`` given, a rotating file."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir is not None:
        from logging.handlers import RotatingFileHandler

        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_dir / "unbound.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
            )
        )

    logging.basicConfig(format="%(message)s", handlers=handlers, level=level.upper())

    renderer = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "unbound") -> Any:
    return structlog.get_logger(name)
