# Data layer: migrations and seed. Build context is backend/ so the shared
# pyproject.toml and uv.lock are visible.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv

WORKDIR /app

# The interpreter comes from the base image; uv must not download its own.
ENV UV_PYTHON_DOWNLOADS=0
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache
ENV PATH="/app/.venv/bin:$PATH"

COPY database/alembic.ini .
COPY database/models.py .
COPY database/seed.py .
COPY database/alembic ./alembic

# Точка входа — сам alembic: контейнером можно ходить по ревизиям.
ENTRYPOINT ["alembic"]
CMD ["upgrade", "head"]
