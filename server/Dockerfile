# T014 — single self-contained image: uv builder (python deps) + node builder (PWA bundle),
# non-root runtime, SQLite in /data volume, frontend served by FastAPI.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS py-builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

FROM node:22-alpine AS web-builder
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm

# Non-root runtime user
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin shoplist

WORKDIR /app
COPY --from=py-builder /app/.venv /app/.venv
COPY server/app ./app
COPY --from=web-builder /web/dist /srv/static

ENV PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv \
    SHOPLIST_DB=/data/shoplist.db \
    SERVE_FRONTEND_DIR=/srv/static

VOLUME ["/data"]
USER shoplist
EXPOSE 8000

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
