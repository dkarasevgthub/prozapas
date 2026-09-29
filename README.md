# ProЗапас

Складской учёт для компании с несколькими складами: десктоп на PyQt6, API на FastAPI
и отдельная служба, которая держит сканер, весы и принтер этикеток.

Идея: заказ — это перемещение товара между двумя складами. Одна запись, которую две
стороны видят по-разному: кому привезут — принимает, у кого заказали — отгружает.
Направление не хранится, а вычисляется от того, кто смотрит.

```
  Десктоп (PyQt6) ──HTTPS/JSON──► FastAPI ──► PostgreSQL
        │                       RBAC, транзакции, OpenAPI
        │
        └──именованный канал──► Служба устройств ──► сканер · весы · принтер
                                {"cmd":"print"} / {"event":"scan"}

  создал заказ ──► processing ──► резерв ──► shipped ──► списание у отправителя
                                                            │
                     оприходование у получателя ◄── received ◄── сканирование коробок
```

## Эндпоинты

```
POST   /auth/login                       вход, отдаёт JWT
GET    /bootstrap                        склады, роли, права, текущий пользователь
GET    /dashboard                        счётчики и лента событий

GET    /orders                           заказы своего склада
POST   /orders/{id}/accept|decline|cancel   смена статуса        (orders: edit)

GET    /shipments/{order_id}             отгрузка по заказу
POST   /shipments/{order_id}/boxes       коробка со штрихкодом   (shipping: edit)
POST   /shipments/{order_id}/ship        отгрузить, списать      (shipping: edit)

GET    /receipts/{order_id}              приёмка по заказу
POST   /receipts/{order_id}/boxes/{barcode}/receive   скан коробки
POST   /receipts/{order_id}/complete     принять, оприходовать   (receiving: edit)

GET    /stock, /catalog, /users, /permissions   справочники и остатки
```

Всего 46 операций, контракт — [docs/api.md](docs/api.md). Реализованы служебные
эндпоинты и вход, остальные разделы дописываются по одному.

## Каталоги

```
desktop/app       клиент: справочник, остатки, заказы, отгрузка, приёмка, пользователи
desktop/devices   служба устройств: драйверы, канал, очередь печати, симулятор
backend/api       роутеры → сервисы → модели, слои проверяются разбором импортов
backend/database  SQLAlchemy 2.0, 16 таблиц, миграции Alembic
docs              контракт API, архитектура, ТЗ на службу устройств
design            макеты экранов
```

## Окружение

Зависимости ставит [uv](https://docs.astral.sh/uv/) по `pyproject.toml` и `uv.lock`,
свои у `desktop/` и у `backend/`. Виртуальное окружение создаётся в `.venv` рядом
с `pyproject.toml`, активировать его не нужно — команды идут через `uv run`.

```
cd desktop && uv sync             # клиент и служба устройств
uv run main.py                    # приложение; службу поднимет само
uv run python -m devices          # служба отдельно

cd backend && uv sync             # для IDE; сами сервисы живут в Docker
make up                           # база, миграции, API — см. backend/Makefile
make test                         # тесты API на отдельной базе prozapas_test
```

Новая зависимость — `uv add <пакет>` в нужном каталоге; `uv.lock` коммитится.
