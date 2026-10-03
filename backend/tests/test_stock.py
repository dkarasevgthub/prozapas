"""Остатки — api.md §6.5."""
from __future__ import annotations

from decimal import Decimal

from tests import flows
from tests.flows import BOLT, NUT, PIPE, WASHER, WH1, WH3, WH4, problem


def _op(actor, warehouse_id, article, type_, qty, comment=None):
    body = {"article": article, "warehouse_id": warehouse_id, "type": type_, "qty": qty}
    if comment is not None:
        body["comment"] = comment
    return actor.post("/stock/operations", json=body)


def test_list_defaults_to_own_warehouse_in_stock(receiver, wh):
    body = receiver.get("/stock").json()
    assert (body["total"], body["limit"], body["offset"]) == (10, 50, 0)
    names = [r["name"] for r in body["items"]]
    assert names == sorted(names)
    bolt = next(r for r in body["items"] if r["article"] == BOLT)
    assert bolt == {"item_id": bolt["item_id"], "article": BOLT, "code1c": "ТМЦ-00421",
                    "name": "Болт М8×40 ГОСТ 7798", "unit": "шт.",
                    "qty": 800.0, "free": 800.0, "reserved": 0.0, "min_qty": 50.0,
                    "by_warehouse": [{"warehouse_id": wh[WH1], "qty": 800.0, "free": 800.0},
                                     {"warehouse_id": wh[WH4], "qty": 150.0, "free": 150.0}]}


def test_list_other_warehouse(receiver, wh):
    body = receiver.get("/stock", params={"warehouse_id": wh[WH3]}).json()
    assert body["total"] == 10
    assert {r["article"] for r in body["items"]} >= {PIPE, WASHER, NUT}


def test_list_search(packer):
    def articles(q):
        return [r["article"] for r in packer.get("/stock", params={"q": q}).json()["items"]]

    assert articles("труба") == [PIPE]
    assert articles("ТРУБА") == [PIPE]
    assert articles("100512") == [PIPE]
    assert articles("нет такого") == []


def test_list_pagination(receiver):
    first = receiver.get("/stock", params={"limit": 4}).json()
    second = receiver.get("/stock", params={"limit": 4, "offset": 4}).json()
    assert len(first["items"]) == 4 and len(second["items"]) == 4 and first["total"] == 10
    assert {r["item_id"] for r in first["items"]}.isdisjoint(
        {r["item_id"] for r in second["items"]})


def test_free_reflects_reserve(accepted_order, packer, wh):
    row = next(r for r in packer.get("/stock").json()["items"] if r["article"] == PIPE)
    assert (row["qty"], row["reserved"], row["free"]) == (120.5, 12.5, 108.0)
    share = next(s for s in row["by_warehouse"] if s["warehouse_id"] == wh[WH3])
    assert share == {"warehouse_id": wh[WH3], "qty": 120.5, "free": 108.0}


def test_in_stock_and_below_min(receiver, wh):
    """recount в ноль: строка уходит из умолчания, остаётся при in_stock=false и при below_min."""
    assert _op(receiver, wh[WH1], BOLT, "recount", 0).status_code == 201

    def articles(**params):
        return {r["article"] for r in receiver.get("/stock", params=params).json()["items"]}

    assert BOLT not in articles()
    assert BOLT in articles(in_stock=False)
    assert BOLT in articles(below_min=True)
    assert BOLT not in articles(below_min=True, in_stock=True)
    assert articles(below_min=True) == {BOLT, "300098", "300133", "300159", "300210"}


def test_summary(receiver, packer, sender, customer, wh):
    assert receiver.get("/stock/summary").json() == {
        "positions": 10, "below_min": 4, "reserved": 0.0, "free": 1822.0}
    assert packer.get("/stock/summary").json() == {
        "positions": 10, "below_min": 0, "reserved": 0.0, "free": 5760.5}
    order = flows.create_order(customer, wh[WH3])
    flows.accept(sender, order["id"])
    assert packer.get("/stock/summary").json() == {
        "positions": 10, "below_min": 0, "reserved": 112.5, "free": 5648.0}


def test_stock_by_item_puts_own_warehouse_first(receiver, outsider_stockman, wh, sql):
    item_id = sql("SELECT id FROM catalog_item WHERE article = :a", a=BOLT)[0][0]
    rows = receiver.get(f"/stock/{item_id}").json()
    assert [r["warehouse"]["id"] for r in rows] == [wh[WH1], wh[WH4]]
    assert rows[0] == {"warehouse": rows[0]["warehouse"], "qty": 800.0, "free": 800.0,
                       "reserved": 0.0, "min_qty": 50.0}
    assert rows[0]["warehouse"]["code"] == WH1
    others = outsider_stockman.get(f"/stock/{item_id}").json()
    assert [r["warehouse"]["id"] for r in others] == [wh[WH4], wh[WH1]]


def test_stock_by_item_edge_cases(receiver, sender):
    problem(receiver.get("/stock/999999"), 404, "not-found")
    item = sender.post("/catalog", json={"article": "X-200", "name": "Новая", "unit": "шт."}).json()
    assert receiver.get(f"/stock/{item['id']}").json() == []


def test_manual_operations(receiver, wh, sql):
    qty0 = float(flows.balance(sql, BOLT, wh[WH1])[0])
    resp = _op(receiver, wh[WH1], BOLT, "receipt", 10.5, "пришло")
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    assert entry["type"] == "receipt" and entry["delta"] == 10.5
    assert entry["balance_after"] == qty0 + 10.5
    assert entry["doc_type"] is None and entry["doc_id"] is None and entry["comment"] == "пришло"
    assert entry["user"] == {"id": receiver.id, "name": "Соколов П.Н."}
    assert entry["id"] and entry["created_at"]
    assert _op(receiver, wh[WH1], BOLT, "shipment", 0.5).json()["delta"] == -0.5
    assert _op(receiver, wh[WH1], BOLT, "writeoff", 10).json()["balance_after"] == qty0
    recount = _op(receiver, wh[WH1], BOLT, "recount", 500, "инвентаризация").json()
    assert recount["delta"] == 500 - qty0 and recount["balance_after"] == 500
    assert flows.balance(sql, BOLT, wh[WH1]) == (Decimal("500"), Decimal("0"))
    assert [m.type for m in flows.movements(sql, BOLT, wh[WH1])] == [
        "receipt", "shipment", "writeoff", "recount"]


def test_operation_creates_balance_for_new_item(receiver, wh, sql):
    assert flows.balance(sql, PIPE, wh[WH1]) == (Decimal("0"), Decimal("0"))
    assert _op(receiver, wh[WH1], PIPE, "receipt", 0.125).status_code == 201
    assert flows.balance(sql, PIPE, wh[WH1]) == (Decimal("0.125"), Decimal("0"))
    assert sql("SELECT b.min_qty FROM stock_balance b JOIN catalog_item c ON c.id = b.item_id "
               "WHERE c.article = :a AND b.warehouse_id = :w",
               a=PIPE, w=wh[WH1]) == [(Decimal("0"),)]


def test_operation_rejections(receiver, sender, packer, wh, sql, accepted_order):
    problem(_op(receiver, wh[WH3], BOLT, "receipt", 1), 422, "unprocessable")
    problem(_op(receiver, wh[WH1], "no-such", "receipt", 1), 404, "not-found")
    problem(_op(receiver, wh[WH1], BOLT, "shipment", 0), 422, "unprocessable")
    problem(_op(receiver, wh[WH1], BOLT, "receipt", 0), 422, "unprocessable")
    problem(_op(receiver, wh[WH1], BOLT, "receipt", -1), 422, "unprocessable")
    body = problem(_op(receiver, wh[WH1], BOLT, "writeoff", 800.5), 409, "insufficient-stock")
    assert body["positions"] == [{"article": BOLT, "requested": 800.5, "available": 800.0}]
    assert flows.balance(sql, BOLT, wh[WH1])[0] == Decimal("800")
    # На складе 129 после accept: труба 120.5, резерв 12.5, свободно 108.
    problem(_op(packer, wh[WH3], PIPE, "writeoff", 110), 409, "insufficient-stock")
    problem(_op(packer, wh[WH3], PIPE, "recount", 12), 422, "unprocessable")
    assert _op(packer, wh[WH3], PIPE, "recount", 12.5).status_code == 201
    item_id = sql("SELECT id FROM catalog_item WHERE article = :a", a=BOLT)[0][0]
    assert sender.post(f"/catalog/{item_id}/archive").status_code == 204
    problem(_op(receiver, wh[WH1], BOLT, "receipt", 1), 422, "unprocessable")


def test_operation_permissions(customer, admin, wh):
    problem(_op(customer, wh[WH1], BOLT, "receipt", 1), 403, "forbidden")
    assert _op(admin, wh[WH1], BOLT, "receipt", 1).status_code == 201


def test_movements_history(receiver, packer, sender, customer, wh, sql):
    item_id = sql("SELECT id FROM catalog_item WHERE article = :a", a=PIPE)[0][0]
    order = flows.create_order(customer, wh[WH3])
    flows.accept(sender, order["id"])
    assert _op(packer, wh[WH3], PIPE, "writeoff", 1, "брак").status_code == 201

    body = packer.get(f"/stock/{item_id}/movements").json()
    assert body["total"] == 2
    latest, reserve = body["items"]
    assert latest["type"] == "writeoff" and latest["delta"] == -1
    assert latest["comment"] == "брак" and latest["doc_type"] is None
    assert reserve["type"] == "reserve" and reserve["delta"] == 12.5
    assert reserve["doc_type"] == "order" and reserve["doc_id"] == order["id"]
    assert reserve["user"] == {"id": sender.id, "name": "Морозова Е.В."}
    assert packer.get(f"/stock/{item_id}/movements", params={"type": "reserve"}).json()["total"] == 1
    assert packer.get(f"/stock/{item_id}/movements", params={"type": "receipt"}).json()["total"] == 0
    assert packer.get(f"/stock/{item_id}/movements", params={"limit": 1}).json()["items"] == [latest]
    assert receiver.get(f"/stock/{item_id}/movements").json()["total"] == 0
    problem(packer.get("/stock/999999/movements"), 404, "not-found")


def test_movements_sum_matches_balance(receiver, wh, sql):
    """Сверочный запрос из api-sections §6: сумма движений = текущий остаток."""
    for type_, qty in (("receipt", 100), ("writeoff", 30.25), ("recount", 500),
                       ("shipment", 12.5)):
        assert _op(receiver, wh[WH1], BOLT, type_, qty).status_code == 201
    total = sum(m.delta for m in flows.movements(sql, BOLT, wh[WH1]))
    assert Decimal("800") + total == flows.balance(sql, BOLT, wh[WH1])[0] == Decimal("487.5")


def test_stock_requires_login(anon):
    problem(anon.get("/stock"), 401, "unauthorized")
