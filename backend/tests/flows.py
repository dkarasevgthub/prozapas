"""Шаги сценариев и константы стенда для тестов API.

Всё через HTTP тем же путём, что и приложение: заказ → принять → упаковать →
отгрузить → принять коробки → завершить. Проверки состояния базы делаются
прямым SQL через фикстуру `sql`, а не через ORM: так видно ровно то, что
зафиксировал сервер.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

API = "/api/v1"
SEED_PASSWORD = "test-password-1"
JWT_SECRET = "test-secret-" + "0" * 40

#: Матрица прав из api.md §5: роль → раздел → (просмотр, изменение).
MATRIX = {
    "manager": {"orders": (True, True), "shipping": (True, False),
                "receiving": (True, False), "catalog": (True, True),
                "stock": (True, False), "users": (False, False)},
    "stockman": {"orders": (True, False), "shipping": (True, True),
                 "receiving": (True, True), "catalog": (True, False),
                 "stock": (True, True), "users": (False, False)},
    "admin": {section: (True, True) for section in
              ("orders", "shipping", "receiving", "catalog", "stock", "users")},
}
SECTIONS = ["orders", "shipping", "receiving", "catalog", "stock", "users"]

# Коды складов из seed.py. Идентификаторы берутся из /bootstrap (фикстура wh).
WH1 = "128"        # Склад №1 — заказчик и получатель в сценариях
WH3 = "129"        # Склад №3 — отправитель
WH4 = "130"        # Склад №4 — посторонний
WHC = "131"        # Центральный склад

# Артикулы из seed.py с остатками на складе-отправителе (129).
PIPE = "100512"    # труба, метры, 120.5 на 129
WASHER = "100655"  # шайба, 3200 на 129
NUT = "100433"     # гайка, 600 на 129
BELT = "201204"    # ремень, 25 на 129 — для нехватки остатка
BOLT = "100421"    # болт, 800 на 128 и 150 на 130
PUMP = "300091"    # насос, только на 131

PIPE_WEIGHT = 1.6
WASHER_WEIGHT = 0.005


def positions(*pairs) -> list[dict]:
    return [{"article": article, "qty": qty} for article, qty in pairs]


def create_order(customer, from_warehouse_id, lines=None, comment=None, key=None):
    body = {"from_warehouse_id": from_warehouse_id,
            "positions": lines or positions((PIPE, 12.5), (WASHER, 100))}
    if comment is not None:
        body["comment"] = comment
    resp = customer.post("/orders", json=body,
                         headers={"Idempotency-Key": key or str(uuid.uuid4())})
    assert resp.status_code == 201, resp.text
    return resp.json()


def accept(sender, order_id):
    resp = sender.post(f"/orders/{order_id}/accept",
                       headers={"If-Match": sender.etag(f"/orders/{order_id}")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def decline(sender, order_id, reason=None):
    resp = sender.post(f"/orders/{order_id}/decline", json={"reason": reason},
                       headers={"If-Match": sender.etag(f"/orders/{order_id}")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def cancel(customer, order_id, reason=None):
    resp = customer.post(f"/orders/{order_id}/cancel", json={"reason": reason},
                         headers={"If-Match": customer.etag(f"/orders/{order_id}")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def pack(packer, order_id, article, qty, weight):
    resp = packer.post(f"/shipments/{order_id}/boxes",
                       json={"article": article, "qty": qty, "weight": weight})
    assert resp.status_code == 201, resp.text
    return resp.json()


def ship(packer, order_id):
    resp = packer.post(f"/shipments/{order_id}/ship",
                       headers={"If-Match": packer.etag(f"/shipments/{order_id}")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def receive(receiver, order_id, barcode, actual_weight):
    resp = receiver.post(f"/receipts/{order_id}/boxes/{barcode}/receive",
                         json={"actual_weight": actual_weight})
    assert resp.status_code == 200, resp.text
    return resp.json()


def complete(receiver, order_id):
    resp = receiver.post(f"/receipts/{order_id}/complete",
                         headers={"If-Match": receiver.etag(f"/receipts/{order_id}")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def balance(sql, article, warehouse_id) -> tuple[Decimal, Decimal]:
    """(qty, reserved) по складу; нет строки — нули, как и трактует сервер."""
    rows = sql("SELECT b.qty, b.reserved FROM stock_balance b "
               "JOIN catalog_item c ON c.id = b.item_id "
               "WHERE c.article = :article AND b.warehouse_id = :wh",
               article=article, wh=warehouse_id)
    if not rows:
        return Decimal("0"), Decimal("0")
    return rows[0][0], rows[0][1]


def movements(sql, article, warehouse_id) -> list:
    return sql("SELECT m.type, m.delta, m.balance_after, m.doc_type, m.doc_id, "
               "m.comment, m.user_id FROM stock_movement m "
               "JOIN catalog_item c ON c.id = m.item_id "
               "WHERE c.article = :article AND m.warehouse_id = :wh "
               "ORDER BY m.id", article=article, wh=warehouse_id)


def problem(resp, status: int, kind: str) -> dict:
    """Ответ — problem+json с ожидаемым кодом и типом (api.md §3.3, §4)."""
    assert resp.status_code == status, f"{resp.status_code}: {resp.text}"
    assert resp.headers["content-type"].startswith("application/problem+json"), \
        resp.headers.get("content-type")
    body = resp.json()
    assert body["type"] == f"https://prozapas/errors/{kind}", body
    assert body["status"] == status, body
    assert body["title"], body
    return body
