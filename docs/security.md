# Security model

Unbound handles two classes of sensitive data: **your Unbound login** and **your Audible
account credentials/tokens**. This document explains how each is protected.

## App authentication

- Single admin account created on first run; the schema supports adding users/roles later.
- Passwords are hashed with **Argon2id** (`argon2-cffi`). Hashes are re-upgraded on login if
  parameters change.
- Sessions are signed **JWTs** carried in an **HttpOnly, Secure, SameSite=strict** cookie
  (`unbound_session`). `Secure` can be disabled (`UNBOUND_COOKIE_SECURE=false`) only for local
  HTTP testing.
- **Idle auto-logout**: the cookie expires after 2h of inactivity (`UNBOUND_IDLE_TIMEOUT_SECONDS`).
  The SPA renews it via `/api/auth/refresh` only while the admin is actually interacting —
  background polling never extends a session. An absolute lifetime
  (`UNBOUND_SESSION_TTL_SECONDS`, default 7 days) forces re-login even for active sessions.
- All state-changing and data endpoints require an authenticated admin. `/api/health` is the
  only unauthenticated route. `/api/stats` accepts a session **or** a read-only API key.

## Audible credentials

- During **guided** linking your email/password are sent to the backend, used **once** to
  register a device with Audible, and then **discarded** — they are never written to disk or DB.
- During **external** linking the backend never sees your password at all; you authenticate on
  Amazon directly and return only the OAuth response URL.
- What *is* stored is the resulting **device auth blob** (tokens + device private key) and,
  optionally, cached **activation bytes** — both **encrypted at rest**.

## Encryption at rest

- A master key (`UNBOUND_SECRET_KEY`) is provided via env, or generated once and persisted to
  `<data>/secret.key` (mode `600`). **Set it explicitly in production** so it survives volume
  resets and can be rotated deliberately.
- From it, an AES-256-GCM key is derived via **HKDF-SHA256**. All secret blobs are stored as
  versioned, authenticated ciphertext (`SecretBox`, see `app/core/security.py`).
- The key's short **fingerprint** is recorded in settings. If the master key changes, Unbound
  logs a `secret_key_rotated` warning at startup (existing ciphertext may be undecryptable —
  re-link affected accounts).

## Logging hygiene

- A structlog processor (`app/core/logging.py`) redacts known sensitive keys (passwords, tokens,
  keys, IVs, vouchers, cookies, OTPs) and token-shaped values before anything is written.
- Secrets are never included in API responses.

## In transit

- The SPA and API share one origin (one process serves both); put Unbound behind a
  TLS-terminating reverse proxy for any non-localhost exposure. If your proxy buffers
  responses, disable buffering for `/api/events/stream` (SSE).

## Key rotation (outline)

1. Decrypt existing blobs with the old key.
2. Set the new `UNBOUND_SECRET_KEY`.
3. Re-encrypt and persist. (A CLI helper for this is planned; until then, unlink and re-link
   accounts after rotating.)
