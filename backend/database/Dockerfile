# Слой данных: миграции и сид. Контекст сборки — backend/, ради общего
# requirements.txt.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY database/alembic.ini .
COPY database/models.py .
COPY database/seed.py .
COPY database/alembic ./alembic

# Точка входа — сам alembic: контейнером можно ходить по ревизиям.
ENTRYPOINT ["alembic"]
CMD ["upgrade", "head"]
