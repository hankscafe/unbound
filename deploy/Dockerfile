# Unbound — single image: FastAPI (API + in-process worker) also serves the built SPA.
# ffmpeg is required for AAX/AAXC decryption.

# --- Stage 1: build the frontend --------------------------------------------
# Debian (glibc) base + a clean `npm install` (no lockfile in the build): both `npm ci`
# and lockfile-driven installs trip the npm optional-dependency bug for rollup's native
# binary (npm/cli#4828, "Exit handler never called"). A fresh resolve installs the
# correct platform binary reliably.
FROM node:20-slim AS frontend
WORKDIR /app
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: backend + static SPA -------------------------------------------
FROM python:3.12-slim

# ffmpeg >= 4.4 (Debian bookworm ships 5.x) handles AAX (activation_bytes) and
# AAXC (audible_key/audible_iv) decryption.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

COPY --from=frontend /app/dist ./static

ENV UNBOUND_DATA_DIR=/data \
    UNBOUND_DATABASE_URL=sqlite:////data/unbound.db \
    UNBOUND_ENVIRONMENT=production \
    UNBOUND_STATIC_DIR=/app/static

VOLUME ["/data"]
EXPOSE 8000

# Healthcheck hits the open liveness endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
