# API. Контекст сборки — backend/, ради общего requirements.txt.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Модели лежат рядом, а не внутри api/: их же читает alembic.
COPY database/models.py ./database/models.py
COPY api ./api

# Пакет database импортируется как есть; __init__ ему не нужен, но без него
# слой моделей не собрать в модуль.
RUN touch ./database/__init__.py

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
