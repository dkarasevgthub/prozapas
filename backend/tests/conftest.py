"""Фикстуры: тестовая база, клиент, действующие лица.

База `prozapas_test` создаётся на том же Postgres, что и стенд (`make up`):
миграции и сид прогоняются один раз на сессию тем же кодом, что и в Docker.
Каждый тест идёт внутри одной внешней транзакции, которая откатывается по
завершении: коммиты сервера превращаются в SAVEPOINT, данные между тестами
не текут, а сид остаётся нетронутым.

Подключение — из `backend/.env` (POSTGRES_*) или переменных окружения;
`TEST_DATABASE_URL` переопределяет всё целиком.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests import flows

BACKEND = Path(__file__).resolve().parents[1]
TEST_DB = "prozapas_test"


def _dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    path = BACKEND / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def _urls() -> tuple[str, str]:
    """(служебная база для CREATE DATABASE, тестовая база для приложения)."""
    env = {**_dotenv(), **os.environ}
    if env.get("TEST_DATABASE_URL"):
        url = env["TEST_DATABASE_URL"]
        admin = url.replace("+psycopg", "").rsplit("/", 1)[0] + "/postgres"
        return admin, url
    password = env.get("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("Нужен POSTGRES_PASSWORD: в backend/.env или в окружении")
    base = (f"{env.get('POSTGRES_USER', 'prozapas_user')}:{password}"
            f"@{env.get('POSTGRES_HOST', '127.0.0.1')}:{env.get('POSTGRES_PORT', '5432')}")
    return f"postgresql://{base}/postgres", f"postgresql+psycopg://{base}/{TEST_DB}"


ADMIN_URL, DATABASE_URL = _urls()
# Настройки читаются один раз при первом обращении: задать до импорта api.
os.environ["DATABASE_URL"] = DATABASE_URL
os.environ["JWT_SECRET"] = flows.JWT_SECRET
os.environ.setdefault("APP_VERSION", "1.0.0")


@pytest.fixture(scope="session")
def database() -> str:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {TEST_DB}")
    env = {**os.environ, "DATABASE_URL": DATABASE_URL,
           "SEED_PASSWORD": flows.SEED_PASSWORD,
           "ADMIN_LOGIN": "admin", "ADMIN_EMAIL": "admin@prozapas.ru"}
    subprocess.run([sys.executable, "-m", "alembic",
                    "-c", str(BACKEND / "database" / "alembic.ini"), "upgrade", "head"],
                   check=True, env=env, cwd=BACKEND)
    subprocess.run([sys.executable, str(BACKEND / "database" / "seed.py")],
                   check=True, env=env, cwd=BACKEND / "database")
    return DATABASE_URL


@pytest.fixture(scope="session")
def engine(database):
    return create_engine(database, future=True)


@pytest.fixture(scope="session")
def app(database):
    from api.main import app as fastapi_app
    return fastapi_app


@pytest.fixture
def connection(engine):
    conn = engine.connect()
    outer = conn.begin()
    try:
        yield conn
    finally:
        outer.rollback()
        conn.close()


@pytest.fixture
def sql(connection):
    """Прямой SQL внутри транзакции теста: видит всё, что зафиксировал сервер."""
    def run(statement: str, **params):
        result = connection.execute(text(statement), params)
        return result.all() if result.returns_rows else []
    return run


@pytest.fixture
def client(app, connection):
    from api.db import get_session

    factory = sessionmaker(bind=connection, join_transaction_mode="create_savepoint",
                           expire_on_commit=False)

    def session_for_request():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_for_request
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class Actor:
    """Вошедший пользователь: запросы с его токеном и разбор ответа входа."""

    def __init__(self, client: TestClient, login: str, payload: dict):
        self.client = client
        self.login = login
        self.payload = payload
        self.token: str = payload["access"]
        self.refresh: str = payload["refresh"]
        self.user: dict = payload["user"]
        self.id: int = payload["user"]["id"]
        self.warehouse_id: int | None = (payload["warehouse"] or {}).get("id")

    def headers(self, extra: dict | None = None) -> dict:
        merged = {"Authorization": f"Bearer {self.token}"}
        merged.update(extra or {})
        return merged

    def request(self, method: str, path: str, **kw):
        kw["headers"] = self.headers(kw.get("headers"))
        return self.client.request(method, flows.API + path, **kw)

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.request("POST", path, **kw)

    def patch(self, path: str, **kw):
        return self.request("PATCH", path, **kw)

    def put(self, path: str, **kw):
        return self.request("PUT", path, **kw)

    def delete(self, path: str, **kw):
        return self.request("DELETE", path, **kw)

    def etag(self, path: str) -> str:
        resp = self.get(path)
        assert resp.status_code == 200, resp.text
        assert "ETag" in resp.headers, f"нет ETag у {path}"
        return resp.headers["ETag"]


def login(client: TestClient, login_: str, password: str = flows.SEED_PASSWORD):
    return client.post(flows.API + "/auth/login",
                       json={"login": login_, "password": password})


@pytest.fixture
def actor(client):
    def make(login_: str, password: str = flows.SEED_PASSWORD) -> Actor:
        resp = login(client, login_, password)
        assert resp.status_code == 200, f"вход {login_}: {resp.status_code} {resp.text}"
        return Actor(client, login_, resp.json())
    return make


@pytest.fixture
def anon(client):
    """Запросы без токена."""
    class Anon:
        def request(self, method, path, **kw):
            return client.request(method, flows.API + path, **kw)

        def get(self, path, **kw):
            return self.request("GET", path, **kw)

        def post(self, path, **kw):
            return self.request("POST", path, **kw)
    return Anon()


# Действующие лица из seed.py: роль и склад подобраны под сценарий
# «Склад №1 заказывает у Склада №3, Склад №4 посторонний».
@pytest.fixture
def admin(actor) -> Actor:            # администратор из ADMIN_LOGIN, склад 128
    return actor("admin")


@pytest.fixture
def customer(actor) -> Actor:         # менеджер склада 128: создаёт и отменяет
    return actor("o.egorova")


@pytest.fixture
def receiver(actor) -> Actor:         # кладовщик склада 128: принимает
    return actor("p.sokolov")


@pytest.fixture
def sender(actor) -> Actor:           # менеджер склада 129: принимает заказ к сборке
    return actor("e.morozova")


@pytest.fixture
def packer(actor) -> Actor:           # кладовщик склада 129: пакует и отгружает
    return actor("p.nikitin")


@pytest.fixture
def outsider(actor) -> Actor:         # менеджер склада 130: не видит ничего
    return actor("t.fedorova")


@pytest.fixture
def outsider_stockman(actor) -> Actor:  # кладовщик склада 130
    return actor("r.titov")


@pytest.fixture
def central_admin(actor) -> Actor:    # администратор Центрального склада 131
    return actor("i.kuznetsov")


@pytest.fixture
def wh(admin) -> dict[str, int]:
    """Код склада → id, из /bootstrap."""
    resp = admin.get("/bootstrap")
    assert resp.status_code == 200, resp.text
    return {w["code"]: w["id"] for w in resp.json()["warehouses"]}


@pytest.fixture
def accepted_order(customer, sender, wh) -> dict:
    """Заказ 128 ← 129 (труба 12.5 м + шайба 100 шт.), принятый к сборке."""
    order = flows.create_order(customer, wh[flows.WH3])
    return flows.accept(sender, order["id"])


@pytest.fixture
def shipped_order(accepted_order, packer) -> dict:
    """Тот же заказ, упакованный целиком в три коробки и отгруженный.

    Коробки: труба 7.5 м + 5 м (одна позиция в двух коробках), шайбы 100 шт.
    """
    order_id = accepted_order["id"]
    boxes = [flows.pack(packer, order_id, flows.PIPE, 7.5, 12.0),
             flows.pack(packer, order_id, flows.PIPE, 5.0, 8.0),
             flows.pack(packer, order_id, flows.WASHER, 100, 0.5)]
    result = flows.ship(packer, order_id)
    result["boxes"] = boxes
    return result
