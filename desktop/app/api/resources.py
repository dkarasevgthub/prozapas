"""Методы API по разделам — ровно то, что описано в docs/api.md.

Склад пользователя нигде не передаётся: он в токене, сервер подставляет фильтр
видимости сам. Поэтому в сигнатурах его нет и быть не должно.

Изменяющие операции документов принимают `version` и отправляют его в `If-Match`.
Значение берётся из самого документа — в теле оно уже есть, отдельно запоминать
заголовок не нужно.
"""
from __future__ import annotations

from .transport import Transport, new_idempotency_key


class Resources:
    def __init__(self, transport: Transport):
        self.t = transport

    # ── служебные ─────────────────────────────────────────────
    def health(self):
        return self.t.get("/health")

    def version(self):
        return self.t.get("/version")

    def bootstrap(self):
        """Склады, роли, разделы, права, текущий пользователь — одним запросом."""
        return self.t.get("/bootstrap")

    def dashboard(self):
        return self.t.get("/dashboard")

    # ── вход ──────────────────────────────────────────────────
    def login(self, login: str, password: str):
        return self.t.login(login, password)

    def logout(self):
        return self.t.logout()

    def me(self):
        return self.t.get("/auth/me")

    # ── заказы ────────────────────────────────────────────────
    def orders(self, tab: str, *, status=None, warehouse_id=None,
               responsible_id=None, created_from=None, created_to=None,
               q=None, limit=50, offset=0):
        """tab: outgoing — мы заказали и принимаем; incoming — мы отгружаем."""
        return self.t.get("/orders", tab=tab, status=status,
                          warehouse_id=warehouse_id, responsible_id=responsible_id,
                          created_from=created_from, created_to=created_to,
                          q=q, limit=limit, offset=offset)

    def create_order(self, from_warehouse_id: int, positions: list, comment: str = ""):
        """positions: [{"article": …, "qty": …}]. Склад-заказчик — наш, из токена."""
        return self.t.post("/orders",
                           {"from_warehouse_id": from_warehouse_id,
                            "positions": positions, "comment": comment},
                           idempotency_key=new_idempotency_key())

    def order(self, order_id: int):
        return self.t.get(f"/orders/{order_id}")

    def order_history(self, order_id: int):
        return self.t.get(f"/orders/{order_id}/history")

    def accept_order(self, order_id: int, version: int):
        return self.t.post(f"/orders/{order_id}/accept", if_match=version)

    def decline_order(self, order_id: int, version: int, reason: str = ""):
        return self.t.post(f"/orders/{order_id}/decline", {"reason": reason},
                           if_match=version)

    def cancel_order(self, order_id: int, version: int, reason: str = ""):
        return self.t.post(f"/orders/{order_id}/cancel", {"reason": reason},
                           if_match=version)

    # ── отгрузка ──────────────────────────────────────────────
    def shipments(self, *, status=None, q=None, weight_min=None, weight_max=None,
                  created_from=None, created_to=None, shipped_from=None,
                  shipped_to=None, limit=50, offset=0):
        return self.t.get("/shipments", status=status, q=q,
                          weight_min=weight_min, weight_max=weight_max,
                          created_from=created_from, created_to=created_to,
                          shipped_from=shipped_from, shipped_to=shipped_to,
                          limit=limit, offset=offset)

    def shipment(self, order_id: int):
        return self.t.get(f"/shipments/{order_id}")

    def pack_box(self, order_id: int, article: str, qty: float, weight: float):
        """Штрихкод выдаёт сервер и возвращает в ответе."""
        return self.t.post(f"/shipments/{order_id}/boxes",
                           {"article": article, "qty": qty, "weight": weight})

    def delete_box(self, order_id: int, box_id: int):
        return self.t.delete(f"/shipments/{order_id}/boxes/{box_id}")

    def ship(self, order_id: int, version: int):
        """Частичная отгрузка разрешена: ответ содержит недостачу по позициям."""
        return self.t.post(f"/shipments/{order_id}/ship", if_match=version)

    # ── приёмка ───────────────────────────────────────────────
    def receipts(self, *, status=None, q=None, weight_min=None, weight_max=None,
                 created_from=None, created_to=None, shipped_from=None,
                 shipped_to=None, limit=50, offset=0):
        return self.t.get("/receipts", status=status, q=q,
                          weight_min=weight_min, weight_max=weight_max,
                          created_from=created_from, created_to=created_to,
                          shipped_from=shipped_from, shipped_to=shipped_to,
                          limit=limit, offset=offset)

    def receipt(self, order_id: int):
        """Настоящие коробки отправителя — те самые, что он упаковал."""
        return self.t.get(f"/receipts/{order_id}")

    def receive_box(self, order_id: int, barcode: str, actual_weight: float):
        """Идемпотентно: коробка уже принята — вернётся как есть."""
        return self.t.post(f"/receipts/{order_id}/boxes/{barcode}/receive",
                           {"actual_weight": actual_weight})

    def undo_receive(self, order_id: int, barcode: str):
        return self.t.delete(f"/receipts/{order_id}/boxes/{barcode}/receive")

    def complete_receipt(self, order_id: int, version: int):
        """Непринятые коробки становятся зафиксированной недостачей."""
        return self.t.post(f"/receipts/{order_id}/complete", if_match=version)

    # ── остатки ───────────────────────────────────────────────
    def stock(self, *, q=None, warehouse_id=None, below_min=None,
              in_stock=None, limit=50, offset=0):
        """in_stock по умолчанию true на сервере; с below_min его надо снимать."""
        return self.t.get("/stock", q=q, warehouse_id=warehouse_id,
                          below_min=below_min, in_stock=in_stock,
                          limit=limit, offset=offset)

    def stock_summary(self):
        return self.t.get("/stock/summary")

    def stock_by_warehouse(self, item_id: int):
        return self.t.get(f"/stock/{item_id}")

    def movements(self, item_id: int, *, type=None, limit=50, offset=0):
        return self.t.get(f"/stock/{item_id}/movements", type=type,
                          limit=limit, offset=offset)

    def stock_operation(self, article: str, warehouse_id: int, type: str,
                        qty: float, comment: str = ""):
        """Для recount qty — новое значение остатка, а не дельта."""
        return self.t.post("/stock/operations",
                           {"article": article, "warehouse_id": warehouse_id,
                            "type": type, "qty": qty, "comment": comment})

    # ── справочник ────────────────────────────────────────────
    def catalog(self, *, q=None, archived=False, limit=50, offset=0):
        return self.t.get("/catalog", q=q, archived=archived,
                          limit=limit, offset=offset)

    def create_item(self, article: str, name: str, unit: str, *,
                    code1c: str = "", unit_weight: float = 0):
        return self.t.post("/catalog", {"article": article, "name": name,
                                        "unit": unit, "code1c": code1c,
                                        "unit_weight": unit_weight})

    def item(self, item_id: int):
        return self.t.get(f"/catalog/{item_id}")

    def update_item(self, item_id: int, **fields):
        return self.t.patch(f"/catalog/{item_id}", fields)

    def archive_item(self, item_id: int):
        return self.t.post(f"/catalog/{item_id}/archive")

    # ── пользователи и права ──────────────────────────────────
    def users(self, *, q=None, role=None, status=None, warehouse_id=None,
              limit=50, offset=0):
        return self.t.get("/users", q=q, role=role, status=status,
                          warehouse_id=warehouse_id, limit=limit, offset=offset)

    def create_user(self, **fields):
        """Обязательно: full_name, login, email, password, role, warehouse_id."""
        return self.t.post("/users", fields)

    def user(self, user_id: int):
        return self.t.get(f"/users/{user_id}")

    def update_user(self, user_id: int, **fields):
        return self.t.patch(f"/users/{user_id}", fields)

    def block_user(self, user_id: int):
        return self.t.post(f"/users/{user_id}/block")

    def unblock_user(self, user_id: int):
        return self.t.post(f"/users/{user_id}/unblock")

    def set_password(self, user_id: int, password: str):
        return self.t.post(f"/users/{user_id}/password", {"password": password})

    def delete_user(self, user_id: int):
        return self.t.delete(f"/users/{user_id}")

    def user_activity(self, user_id: int, *, limit=50, offset=0):
        """Из audit_log. Раздел и формулировку собирает экран."""
        return self.t.get(f"/users/{user_id}/activity", limit=limit, offset=offset)

    def permissions(self):
        return self.t.get("/permissions")

    def save_permissions(self, matrix: list):
        """matrix: [{"role": …, "section": …, "can_view": …, "can_edit": …}]"""
        return self.t.put("/permissions", matrix)
