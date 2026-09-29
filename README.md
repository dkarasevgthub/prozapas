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

POST   /catalog/import, /stock/import    загрузить XML из 1С (CommerceML 2)
GET    /catalog/export, /stock/export    выгрузить XML для 1С
```

Всего 50 операций, контракт — [docs/api.md](docs/api.md). Реализованы все разделы;
`backend/tests` держит их под тестами на настоящем Postgres, включая сверку
живой схемы с [docs/openapi.json](docs/openapi.json).

## Каталоги

```
desktop/app       клиент: справочник, остатки, заказы, отгрузка, приёмка, пользователи
desktop/devices   служба устройств: драйверы, канал, очередь печати, симулятор
backend/api       роутеры → сервисы → модели, слои проверяются разбором импортов
backend/database  SQLAlchemy 2.0, 16 таблиц, миграции Alembic
docs              контракт API, архитектура, ТЗ на службу устройств
```

## Запуск

Три части поднимаются независимо, каждая одной командой из чистого клона.
Ничего подготавливать заранее не нужно: `uv run` сам создаст `.venv` по локу,
а `make up` — свой `.env`.

**API** — Postgres, миграции, данные стенда и сервер, всё в Docker:

```
cd backend && make up
```

Слушает `127.0.0.1:8000`, Swagger — на `/api/v1/docs`. `.env` соберётся из
`.env.example`, если его ещё нет. Вход: логин `admin`, пароль — `SEED_PASSWORD`
из `.env`, он же у всех остальных учёток сида. Остановить, сохранив данные —
`make down`; снести базу и собрать заново — `make reset`.

**Приложение** — клиент; служба устройств поднимается вместе с ним и гаснет
вместе с ним, отдельно её запускать не нужно:

```
cd desktop && uv run main.py
```

Адрес сервера по умолчанию — `http://127.0.0.1:8000/api/v1`, то есть локальный
`make up` подхватывается без настройки. Другой адрес — `PROZAPAS_API` в
`desktop/.env` (см. `desktop/.env.example`).

**Симулятор оборудования** — окно с кнопками вместо сканера, весов и принтера.
Запускают рядом с уже работающим приложением: служба к этому моменту поднята,
симулятор подключается к её каналу, забирает устройство галочкой «Включить
эмуляцию» и вбрасывает события — приложение видит их как настоящие.

```
cd desktop && uv run python -m devices.simulator
```

Подробности — [docs/devices-simulation.md](docs/devices-simulation.md). Там же
про `python -m devices.cli` — то же самое из консоли, без окна.

## Окружение

Зависимости ставит [uv](https://docs.astral.sh/uv/) по `pyproject.toml` и `uv.lock`,
свои у `desktop/` и у `backend/`. Виртуальное окружение создаётся в `.venv` рядом
с `pyproject.toml`, активировать его не нужно — команды идут через `uv run`.

```
cd desktop && uv sync             # поставить зависимости, не запуская клиент
uv run python -m devices          # служба устройств отдельно от приложения

cd backend && uv sync             # для IDE; сами сервисы живут в Docker
make test                         # тесты API на отдельной базе prozapas_test
                                  # остальные цели — в backend/Makefile
```

Новая зависимость — `uv add <пакет>` в нужном каталоге; `uv.lock` коммитится.
