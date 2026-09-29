# API. Build context is backend/ so the shared pyproject.toml and uv.lock are visible.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv

WORKDIR /app

# The interpreter comes from the base image; uv must not download its own.
ENV UV_PYTHON_DOWNLOADS=0
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache
ENV PATH="/app/.venv/bin:$PATH"

# Модели лежат рядом, а не внутри api/: их же читает alembic.
COPY database/models.py ./database/models.py
COPY api ./api

# Пакет database импортируется как есть; __init__ ему не нужен, но без него
# слой моделей не собрать в модуль.
RUN touch ./database/__init__.py

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
