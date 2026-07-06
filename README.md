# Unbound

**Unbound** is a self-hosted, UI-first Audible library manager — a modern take on
[Libation](https://github.com/rmcrackan/Libation). It makes it easy to **connect your own
Audible account(s), back up and decrypt the audiobooks you've purchased, and organize them
into a tidy library** — all from a secured dark web UI, with a REST API for dashboards.

> **Personal-use backup only.** Unbound requires *your own* Audible credentials and is intended
> for backing up content you have already purchased, for personal use. Removing DRM may be
> restricted in your jurisdiction and generally violates Audible's Terms of Service. Do not use
> Unbound to redistribute or share audiobooks. You accept these terms during first-run setup.

## Features

- 🔐 **Secured** — first-run admin setup, login page, Argon2 passwords, HttpOnly/Secure session cookies.
- 🔗 **Multiple Audible accounts** — link several at once; each gets its own color **badge**.
- 🧭 **Two linking flows** — guided in-app (email + password, with OTP/CAPTCHA prompts) *or*
  external-browser (log in on Amazon, paste the response URL).
- 📚 **Library sync** — pull your full library; search, filter, and see per-account badges.
- 🚫 **Exclude toggle** — flag titles you never want downloaded.
- ⬇️ **Download → decrypt → tag → move** pipeline — AAXC (per-file voucher) and AAX
  (activation bytes) via ffmpeg, cover art + metadata embedding, Libation-style naming templates.
- 📊 **Dashboard + REST `/api/stats`** — connected/failed/in-progress counts, update banner,
  live activity feed (SSE). Read-only **API keys** make it a drop-in [Homepage](https://gethomepage.dev) widget.
- 🔒 **Secrets encrypted at rest** — Audible device tokens are AES-256-GCM encrypted; raw
  passwords are never stored. Logs are redacted.
- 🪵 **Structured logging** with rotation.

## Quick start (Docker — recommended)

```bash
git clone https://github.com/unbound-app/unbound.git
cd unbound/deploy
cp .env.example .env
# Edit .env: set a strong UNBOUND_SECRET_KEY and UNBOUND_LIBRARY_HOST_PATH.
docker compose up -d --build
```

Open **http://localhost:8080** (or your configured `UNBOUND_HTTP_PORT`). Complete first-run
setup, then add and link an Audible account.

> For plain-HTTP LAN testing, set `UNBOUND_COOKIE_SECURE=false` in `.env`. For anything
> exposed beyond localhost, put Unbound behind a TLS reverse proxy (Caddy/Traefik/nginx) and
> keep secure cookies on.

## Quick start (bare metal)

**Backend** (Python 3.11–3.12, plus `ffmpeg` on PATH):

```bash
cd backend
python -m venv .venv && . .venv/Scripts/activate   # Windows
# source .venv/bin/activate                         # Linux/macOS
pip install -e .
export UNBOUND_SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
export UNBOUND_COOKIE_SECURE=false                  # if not using HTTPS locally
uvicorn app.main:app --reload --port 8000
```

**Frontend** (Node 20+):

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173, proxies /api to :8000
```

For a production bare-metal build, run `npm run build` and serve `frontend/dist` behind a web
server that proxies `/api` to the uvicorn process (see `deploy/nginx.conf`).

## Homepage widget

Create a read API key under **Settings → API keys**, then add to Homepage's `services.yaml`:

```yaml
- Media:
    - Unbound:
        icon: mdi-book-music
        href: http://your-host:8080
        widget:
          type: customapi
          url: http://your-host:8080/api/stats
          headers:
            X-API-Key: unb_your_key_here
          mappings:
            - field: accounts_linked
              label: Linked
            - field: downloaded
              label: Downloaded
            - field: in_progress
              label: In progress
            - field: failed
              label: Failed
```

## Architecture

| Layer     | Tech |
|-----------|------|
| Backend   | Python, **FastAPI**, SQLModel, Alembic; in-process worker pool |
| Audible   | [`audible`](https://github.com/mkb79/Audible) library (auth, device reg, licensing) |
| Decrypt   | `ffmpeg` — `-audible_key/-audible_iv` (AAXC) or `-activation_bytes` (AAX) |
| Frontend  | React + Vite + TypeScript + Tailwind (dark Audible theme), TanStack Query |
| DB        | SQLite by default; Postgres via `UNBOUND_DATABASE_URL` |
| Deploy    | Docker Compose (backend + nginx frontend) or bare metal |

See [`docs/`](docs/) for the security model and configuration reference. The full design lives in
the implementation plan under source control history.

## Development

```bash
# Backend tests (crypto roundtrip, log redaction, full web flow)
cd backend && pip install -e ".[dev]" && pytest -q

# Frontend typecheck + build
cd frontend && npm run typecheck && npm run build
```

## License

AGPL-3.0-or-later. Unbound distributes *software* only — never audiobook content. Built on
`audible` (AGPL-3.0); see its license for network-copyleft implications if you host a modified version.
