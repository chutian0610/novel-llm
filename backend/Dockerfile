# Novel LLM API + Worker image. Build context is the repository root so the
# layered TOML configuration can be baked in (ADR-0002).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /usr/local/bin/

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/src ./src
COPY backend/alembic ./alembic
RUN uv sync --frozen --no-dev

COPY config /app/config

ENV NOVEL_LLM_ALEMBIC_DIR=/app/alembic \
    NOVEL_LLM_CONFIG_DIR=/app/config

EXPOSE 8000
