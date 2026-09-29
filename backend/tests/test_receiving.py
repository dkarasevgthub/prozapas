"""Приёмка — api.md §6.4, §7.3."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests import flows
from tests.flows import NUT, PIPE, WASHER, WH1, WH3, problem


def test_list_shows_shipped_orders_for_the_receiver_only(shipped_order, receiver, packer,
                                                        outsider_stockman, wh):
    order = shipped_order["order"]
    body = receiver.get("/receipts").json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["order_id"] == order["id"] and row["number"] == order["number"]
    assert row["status"] == "progress"
    assert row["from_warehouse"]["id"] == wh[WH3]
    assert (row["boxes_total"], row["boxes_received"]) == (3, 0)
    assert (row["weight_expected"], row["weight_actual"]) == (20.5, 0)
    assert row["responsible"] is None
    assert row["created_at"] and row["shipped_at"]
    assert packer.get("/receipts").json()["items"] == []
    assert outsider_stockman.get("/receipts").json()["items"] == []


def test_list_hides_orders_before_shipping(accepted_order, receiver):
    assert receiver.get("/receipts").json()["total"] == 0
    problem(receiver.get(f"/receipts/{accepted_order['id']}"), 404, "not-found")


def test_list_progress_and_filters(shipped_order, receiver, customer, sender, packer, wh):
    oid = shipped_order["order"]["id"]
    flows.receive(receiver, oid, shipped_order["boxes"][0]["barcode"], 11.5)
    row = receiver.get("/receipts").json()["items"][0]
    assert row["boxes_received"] == 1 and row["weight_actual"] == 11.5
    assert row["responsible"] == {"id": receiver.id, "name": "Соколов П.Н."}

    other = flows.accept(sender, flows.create_order(
        customer, wh[WH3], lines=flows.positions((NUT, 10)))["id"])
    flows.pack(packer, other["id"], NUT, 10, 0.1)
    flows.ship(packer, other["id"])
    flows.complete(receiver, other["id"])
    today = date.today()

    def ids(**params):
        return {r["order_id"] for r in receiver.get("/receipts", params=params).json()["items"]}

    assert ids() == {oid, other["id"]}
    assert ids(status="progress") == {oid}
    assert ids(status="done") == {other["id"]}
    assert ids(q=shipped_order["order"]["number"]) == {oid}
    assert ids(weight_min=10) == {oid}
    assert ids(weight_max=1) == {other["id"]}
    assert ids(shipped_from=(today - timedelta(days=1)).isoformat()) == {oid, other["id"]}
    assert ids(shipped_to=(today - timedelta(days=2)).isoformat()) == set()
    assert ids(created_from=(today + timedelta(days=2)).isoformat()) == set()
    assert receiver.get("/receipts", params={"limit": 1}).json()["total"] == 2


def test_detail_shows_real_boxes(shipped_order, receiver):
    oid = shipped_order["order"]["id"]
    resp = receiver.get(f"/receipts/{oid}")
    assert resp.status_code == 200 and resp.headers["ETag"] == '"1"'
    body = resp.json()
    assert body["order"]["id"] == oid and body["order"]["status"] == "shipped"
    assert body["status"] == "progress" and body["responsible"] is None
    assert body["version"] == 1
    assert [b["barcode"] for b in body["boxes"]] == [b["barcode"] for b in shipped_order["boxes"]]
    first = body["boxes"][0]
    assert first == {"barcode": first["barcode"], "article": PIPE,
                     "name": "Труба стальная 32×2", "qty": 7.5, "weight": 12.0,
                     "received_at": None, "actual_weight": None,
                     "diff_kg": None, "diff_percent": None}


def test_detail_hidden_from_sender_and_outsider(shipped_order, packer, outsider_stockman,
                                               receiver):
    oid = shipped_order["order"]["id"]
    problem(packer.get(f"/receipts/{oid}"), 404, "not-found")
    problem(outsider_stockman.get(f"/receipts/{oid}"), 404, "not-found")
    problem(receiver.get("/receipts/999999"), 404, "not-found")


def test_receive_box_computes_difference(shipped_order, receiver):
    oid = shipped_order["order"]["id"]
    barcode = shipped_order["boxes"][0]["barcode"]
    box = flows.receive(receiver, oid, barcode, 11.7)
    assert box["received_at"] and box["actual_weight"] == 11.7
    assert box["diff_kg"] == pytest.approx(-0.3)
    assert box["diff_percent"] == pytest.approx(-2.5)
    detail = receiver.get(f"/receipts/{oid}").json()
    assert detail["boxes"][0]["received_at"] == box["received_at"]
    assert detail["responsible"] == {"id": receiver.id, "name": "Соколов П.Н."}


def test_receive_is_idempotent_by_barcode(shipped_order, receiver):
    oid = shipped_order["order"]["id"]
    barcode = shipped_order["boxes"][0]["barcode"]
    first = flows.receive(receiver, oid, barcode, 11.7)
    again = flows.receive(receiver, oid, barcode, 99.0)
    assert again == first


def test_receive_rules(shipped_order, receiver, customer, packer, outsider_stockman,
                       sender, wh):
    oid = shipped_order["order"]["id"]
    barcode = shipped_order["boxes"][0]["barcode"]
    path = f"/receipts/{oid}/boxes/{barcode}/receive"
    problem(customer.post(path, json={"actual_weight": 1}), 403, "forbidden")
    problem(packer.post(path, json={"actual_weight": 1}), 404, "not-found")
    problem(outsider_stockman.post(path, json={"actual_weight": 1}), 404, "not-found")
    problem(receiver.post(f"/receipts/{oid}/boxes/NOPE/receive",
                          json={"actual_weight": 1}), 404, "not-found")
    problem(receiver.post(path, json={"actual_weight": 0}), 422, "unprocessable")

    other = flows.accept(sender, flows.create_order(
        customer, wh[WH3], lines=flows.positions((NUT, 1)))["id"])
    flows.pack(packer, other["id"], NUT, 1, 0.01)
    flows.ship(packer, other["id"])
    problem(receiver.post(f"/receipts/{other['id']}/boxes/{barcode}/receive",
                          json={"actual_weight": 1}), 404, "not-found")

    flows.complete(receiver, oid)
    problem(receiver.post(path, json={"actual_weight": 1}), 409, "invalid-transition")


def test_receive_before_shipping(accepted_order, receiver):
    resp = receiver.post(f"/receipts/{accepted_order['id']}/boxes/NOPE/receive",
                         json={"actual_weight": 1})
    assert resp.status_code in (404, 409), resp.text


def test_cancel_receive(shipped_order, receiver, customer):
    oid = shipped_order["order"]["id"]
    barcode = shipped_order["boxes"][0]["barcode"]
    path = f"/receipts/{oid}/boxes/{barcode}/receive"
    flows.receive(receiver, oid, barcode, 11.7)
    problem(customer.delete(path), 403, "forbidden")
    assert receiver.delete(path).status_code == 204
    box = receiver.get(f"/receipts/{oid}").json()["boxes"][0]
    assert box["received_at"] is None and box["actual_weight"] is None
    assert box["diff_kg"] is None
    assert receiver.delete(path).status_code == 204
    problem(receiver.delete(f"/receipts/{oid}/boxes/NOPE/receive"), 404, "not-found")
    flows.complete(receiver, oid)
    problem(receiver.delete(path), 409, "invalid-transition")


def test_complete_books_only_received_boxes(shipped_order, receiver, customer, wh, sql):
    oid = shipped_order["order"]["id"]
    boxes = shipped_order["boxes"]
    pipe_before = flows.balance(sql, PIPE, wh[WH1])
    washer_before = flows.balance(sql, WASHER, wh[WH1])
    flows.receive(receiver, oid, boxes[0]["barcode"], 12.0)     # труба 7.5 м
    flows.receive(receiver, oid, boxes[2]["barcode"], 0.5)      # шайбы 100 шт.

    result = flows.complete(receiver, oid)
    order = result["order"]
    assert order["status"] == "received" and order["accepted_at"] and order["version"] == 4
    assert [b["barcode"] for b in result["missing"]] == [boxes[1]["barcode"]]
    assert result["missing"][0]["received_at"] is None
    assert flows.balance(sql, PIPE, wh[WH1]) == (pipe_before[0] + Decimal("7.5"), Decimal("0"))
    assert flows.balance(sql, WASHER, wh[WH1]) == (washer_before[0] + Decimal("100"),
                                                   Decimal("0"))
    receipt = flows.movements(sql, PIPE, wh[WH1])[-1]
    assert (receipt.type, receipt.delta, receipt.balance_after, receipt.doc_type,
            receipt.doc_id, receipt.user_id) == (
        "receipt", Decimal("7.5"), pipe_before[0] + Decimal("7.5"), "order", oid, receiver.id)
    assert sql("SELECT status, version, accepted_at IS NOT NULL FROM receipt "
               "WHERE order_id = :id", id=oid) == [("done", 2, True)]
    assert receiver.get("/receipts").json()["items"][0]["status"] == "done"
    assert customer.get(f"/orders/{oid}").json()["status"] == "received"
    history = customer.get(f"/orders/{oid}/history").json()
    assert [e["status"] for e in history] == ["created", "processing", "shipped", "received"]
    problem(receiver.post(f"/receipts/{oid}/complete", headers={"If-Match": '"2"'}),
            409, "invalid-transition")


def test_complete_with_nothing_received(shipped_order, receiver, wh, sql):
    oid = shipped_order["order"]["id"]
    before = flows.balance(sql, PIPE, wh[WH1])
    result = flows.complete(receiver, oid)
    assert result["order"]["status"] == "received" and len(result["missing"]) == 3
    assert flows.balance(sql, PIPE, wh[WH1]) == before
    assert flows.movements(sql, PIPE, wh[WH1]) == []


def test_complete_rules(shipped_order, receiver, customer, packer, outsider_stockman):
    oid = shipped_order["order"]["id"]
    path = f"/receipts/{oid}/complete"
    problem(receiver.post(path), 428, "precondition-required")
    problem(receiver.post(path, headers={"If-Match": "x"}), 400, "bad-request")
    problem(receiver.post(path, headers={"If-Match": '"5"'}), 409, "conflict")
    problem(customer.post(path, headers={"If-Match": '"1"'}), 403, "forbidden")
    problem(packer.post(path, headers={"If-Match": '"1"'}), 404, "not-found")
    problem(outsider_stockman.post(path, headers={"If-Match": '"1"'}), 404, "not-found")
    problem(receiver.post("/receipts/999999/complete", headers={"If-Match": '"1"'}),
            404, "not-found")


def test_complete_before_shipping(accepted_order, receiver):
    resp = receiver.post(f"/receipts/{accepted_order['id']}/complete",
                         headers={"If-Match": '"1"'})
    assert resp.status_code in (404, 409), resp.text


def test_end_to_end_balances_reconcile(shipped_order, receiver, wh, sql):
    """Сквозной сценарий (api-sections §5): списано упакованное, оприходовано принятое."""
    oid = shipped_order["order"]["id"]
    boxes = shipped_order["boxes"]
    assert flows.balance(sql, PIPE, wh[WH3]) == (Decimal("108"), Decimal("0"))
    assert flows.balance(sql, WASHER, wh[WH3]) == (Decimal("3100"), Decimal("0"))
    for box in boxes[:2]:
        flows.receive(receiver, oid, box["barcode"], box["weight"])
    flows.complete(receiver, oid)
    assert flows.balance(sql, PIPE, wh[WH1]) == (Decimal("12.5"), Decimal("0"))
    assert flows.balance(sql, WASHER, wh[WH1]) == (Decimal("0"), Decimal("0"))
    assert [m.type for m in flows.movements(sql, PIPE, wh[WH3])] == ["reserve", "shipment"]
    assert [m.type for m in flows.movements(sql, PIPE, wh[WH1])] == ["receipt"]


def test_meters_travel_without_rounding(customer, sender, packer, receiver, wh, sql):
    order = flows.create_order(customer, wh[WH3], lines=flows.positions((PIPE, 12.345)))
    flows.accept(sender, order["id"])
    flows.pack(packer, order["id"], PIPE, 12.345, 19.752)
    flows.ship(packer, order["id"])
    box = receiver.get(f"/receipts/{order['id']}").json()["boxes"][0]
    assert box["qty"] == 12.345 and box["weight"] == 19.752
    flows.receive(receiver, order["id"], box["barcode"], 19.7)
    flows.complete(receiver, order["id"])
    assert flows.balance(sql, PIPE, wh[WH1]) == (Decimal("12.345"), Decimal("0"))
    assert flows.balance(sql, PIPE, wh[WH3])[0] == Decimal("120.5") - Decimal("12.345")


def test_receiving_requires_login(anon):
    problem(anon.get("/receipts"), 401, "unauthorized")
