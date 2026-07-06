# Configuration reference

All settings are environment variables prefixed `UNBOUND_` (or lines in `deploy/.env`).

| Variable | Default | Description |
|----------|---------|-------------|
| `UNBOUND_SECRET_KEY` | *(generated)* | Master key for encrypting secrets at rest. **Set in production.** |
| `UNBOUND_SESSION_SECRET` | = secret key | Signing key for session cookies. |
| `UNBOUND_SESSION_TTL_SECONDS` | `604800` | Session lifetime (7 days). |
| `UNBOUND_COOKIE_SECURE` | `true` | Send session cookie only over HTTPS. Set `false` for local HTTP. |
| `UNBOUND_DATA_DIR` | `./data` | Base dir for DB, logs, downloads, key file. |
| `UNBOUND_LIBRARY_DIR` | `./data/library` | Default library output root. |
| `UNBOUND_DOWNLOADS_DIR` | `./data/downloads` | Temp dir for encrypted downloads. |
| `UNBOUND_DATABASE_URL` | `sqlite:///./data/unbound.db` | SQLAlchemy URL. Postgres supported. |
| `UNBOUND_MAX_CONCURRENT_DOWNLOADS` | `2` | Worker download concurrency. |
| `UNBOUND_FFMPEG_PATH` | `ffmpeg` | Path to the ffmpeg binary (needs ≥4.4). |
| `UNBOUND_QUEUE_BACKEND` | `db` | `db` (in-process) or `redis`. |
| `UNBOUND_REDIS_URL` | `redis://localhost:6379/0` | Used when queue backend is `redis`. |
| `UNBOUND_UPDATE_CHECK_ENABLED` | `true` | Poll GitHub releases for a newer version. |
| `UNBOUND_GITHUB_REPO` | `unbound-app/unbound` | Repo checked for updates. |
| `UNBOUND_CORS_ORIGINS` | *(empty)* | Comma-separated dev origins; unneeded in prod (same origin). |
| `UNBOUND_DEBUG` | `false` | Verbose logging + console-formatted logs. |
| `UNBOUND_ENVIRONMENT` | `production` | `production` emits JSON logs. |

## Naming templates

Library profiles use these tokens in `folder_template` / `filename_template`:

`{author}` `{title}` `{subtitle}` `{narrator}` `{series}` `{series_seq}` `{asin}` `{year}`

Empty tokens collapse (a missing `{series}` won't leave an empty folder). All components are
sanitized for cross-platform safety. Example:

- `folder_template = {author}/{series}`
- `filename_template = {series_seq} - {title}`
- → `Brandon Sanderson/Mistborn/1 - The Final Empire.m4b`
