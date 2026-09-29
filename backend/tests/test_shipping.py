"""Отгрузка — api.md §6.3, §7.2, §8.1."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from tests import flows
from tests.flows import NUT, PIPE, WASHER, WH1, WH3, problem


def test_list_shows_only_orders_we_ship(accepted_order, packer, customer, outsider, wh):
    body = packer.get("/shipments").json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["order_id"] == accepted_order["id"]
    assert row["number"] == accepted_order["number"]
    assert row["status"] == "progress"
    assert row["to_warehouse"]["id"] == wh[WH1]
    assert (row["positions_total"], row["positions_packed"]) == (2, 0)
    assert (row["weight_expected"], row["weight_packed"]) == (20.5, 0.0)
    assert row["responsible"] is None
    assert row["created_at"] and row["shipped_at"] is None
    assert customer.get("/shipments").json()["items"] == []
    assert outsider.get("/shipments").json()["items"] == []


def test_list_shows_progress_and_responsible(accepted_order, packer):
    oid = accepted_order["id"]
    flows.pack(packer, oid, WASHER, 100, 0.5)
    row = packer.get("/shipments").json()["items"][0]
    assert (row["positions_packed"], row["positions_total"]) == (1, 2)
    assert row["weight_packed"] == 0.5
    assert row["responsible"] == {"id": packer.id, "name": "Никитин П.Р."}
    flows.pack(packer, oid, PIPE, 12.5, 20.0)
    flows.ship(packer, oid)
    row = packer.get("/shipments").json()["items"][0]
    assert row["status"] == "done" and row["positions_packed"] == 2 and row["shipped_at"]


def test_list_excludes_orders_not_yet_accepted(customer, packer, wh):
    flows.create_order(customer, wh[WH3])
    assert packer.get("/shipments").json()["total"] == 0


def test_list_filters(customer, sender, packer, wh):
    in_progress = flows.accept(sender, flows.create_order(customer, wh[WH3])["id"])
    done = flows.accept(sender, flows.create_order(
        customer, wh[WH3], lines=flows.positions((NUT, 10)))["id"])
    flows.pack(packer, done["id"], NUT, 10, 0.1)
    flows.ship(packer, done["id"])
    today = date.today()

    def ids(**params):
        return {r["order_id"] for r in packer.get("/shipments", params=params).json()["items"]}

    assert ids() == {in_progress["id"], done["id"]}
    assert ids(status="progress") == {in_progress["id"]}
    assert ids(status="done") == {done["id"]}
    assert ids(status="waiting") == set()
    assert ids(q=in_progress["number"]) == {in_progress["id"]}
    assert ids(weight_min=20) == {in_progress["id"]}
    assert ids(weight_max=1) == {done["id"]}
    assert ids(weight_min=0, weight_max=100) == {in_progress["id"], done["id"]}
    assert ids(created_from=(today - timedelta(days=1)).isoformat()) == {in_progress["id"], done["id"]}
    assert ids(created_to=(today - timedelta(days=2)).isoformat()) == set()
    assert ids(shipped_from=(today - timedelta(days=1)).isoformat()) == {done["id"]}
    assert ids(shipped_to=(today - timedelta(days=2)).isoformat()) == set()
    page = packer.get("/shipments", params={"limit": 1}).json()
    assert page["total"] == 2 and len(page["items"]) == 1


def test_detail_shows_to_pack_and_boxes(accepted_order, packer):
    oid = accepted_order["id"]
    resp = packer.get(f"/shipments/{oid}")
    assert resp.status_code == 200 and resp.headers["ETag"] == '"1"'
    body = resp.json()
    assert body["order"]["id"] == oid and body["order"]["status"] == "processing"
    assert body["status"] == "progress" and body["responsible"] is None
    assert body["version"] == 1
    assert sorted(body["to_pack"], key=lambda line: line["article"]) == [
        {"article": PIPE, "name": "Труба стальная 32×2", "unit": "м", "unit_weight": 1.6,
         "ordered": 12.5, "packed": 0.0, "remaining": 12.5},
        {"article": WASHER, "name": "Шайба М8 ГОСТ 6402", "unit": "шт.",
         "unit_weight": 0.005, "ordered": 100.0, "packed": 0.0, "remaining": 100.0}]
    assert body["boxes"] == []

    box = flows.pack(packer, oid, PIPE, 7.5, 12.0)
    body = packer.get(f"/shipments/{oid}").json()
    line = next(line for line in body["to_pack"] if line["article"] == PIPE)
    assert (line["packed"], line["remaining"]) == (7.5, 5.0)
    assert body["boxes"] == [box]
    assert body["responsible"] == {"id": packer.id, "name": "Никитин П.Р."}


def test_detail_hidden_from_customer_and_outsider(accepted_order, customer, outsider, packer):
    oid = accepted_order["id"]
    problem(customer.get(f"/shipments/{oid}"), 404, "not-found")
    problem(outsider.get(f"/shipments/{oid}"), 404, "not-found")
    problem(packer.get("/shipments/999999"), 404, "not-found")


def test_detail_absent_before_accept(customer, packer, wh):
    order = flows.create_order(customer, wh[WH3])
    problem(packer.get(f"/shipments/{order['id']}"), 404, "not-found")


def test_box_barcode_is_issued_by_server(accepted_order, packer, wh):
    oid, number = accepted_order["id"], accepted_order["number"]
    first = flows.pack(packer, oid, PIPE, 5, 8.0)
    second = flows.pack(packer, oid, PIPE, 5, 8.0)
    third = flows.pack(packer, oid, PIPE, 2.5, 4.0)
    washers = flows.pack(packer, oid, WASHER, 100, 0.5)
    base = f"WH{wh[WH1]:03d}{number}{PIPE}"
    assert first["barcode"] == base
    assert second["barcode"] == f"{base}-02"
    assert third["barcode"] == f"{base}-03"
    assert washers["barcode"] == f"WH{wh[WH1]:03d}{number}{WASHER}"
    assert first == {"id": first["id"], "barcode": base, "article": PIPE,
                     "name": "Труба стальная 32×2", "qty": 5.0, "weight": 8.0,
                     "created_at": first["created_at"]}
    assert len({b["barcode"] for b in (first, second, third, washers)}) == 4


def test_barcodes_stay_unique_after_deleting_a_middle_box(accepted_order, packer):
    """Штрихкоды уникальны при упаковке позиции в несколько коробок (api.md §11)."""
    oid = accepted_order["id"]
    flows.pack(packer, oid, PIPE, 4, 6.4)
    middle = flows.pack(packer, oid, PIPE, 4, 6.4)
    flows.pack(packer, oid, PIPE, 4, 6.4)
    assert packer.delete(f"/shipments/{oid}/boxes/{middle['id']}").status_code == 204
    resp = packer.post(f"/shipments/{oid}/boxes",
                       json={"article": PIPE, "qty": 0.5, "weight": 0.8})
    assert resp.status_code == 201, resp.text
    barcodes = [b["barcode"] for b in packer.get(f"/shipments/{oid}").json()["boxes"]]
    assert len(barcodes) == len(set(barcodes)) == 3


def test_box_limits_and_validation(accepted_order, packer):
    oid = accepted_order["id"]
    path = f"/shipments/{oid}/boxes"
    flows.pack(packer, oid, PIPE, 12.5, 20.0)
    problem(packer.post(path, json={"article": PIPE, "qty": 0.001, "weight": 0.01}),
            422, "unprocessable")
    problem(packer.post(path, json={"article": NUT, "qty": 1, "weight": 0.01}),
            422, "unprocessable")
    problem(packer.post(path, json={"article": WASHER, "qty": 0, "weight": 1}),
            422, "unprocessable")
    problem(packer.post(path, json={"article": WASHER, "qty": 1, "weight": 0}),
            422, "unprocessable")
    problem(packer.post(path, json={"article": WASHER, "qty": 101, "weight": 1}),
            422, "unprocessable")
    assert len(packer.get(f"/shipments/{oid}").json()["boxes"]) == 1


def test_box_weight_is_checked_against_quantity(accepted_order, packer):
    """Количество и вес сверяются между собой (api.md §6.3): 1 м трубы весит 1.6 кг, не тонну."""
    resp = packer.post(f"/shipments/{accepted_order['id']}/boxes",
                       json={"article": PIPE, "qty": 1, "weight": 1000})
    problem(resp, 422, "unprocessable")


def test_box_access_rules(accepted_order, customer, outsider, sender, receiver,
                          outsider_stockman):
    path = f"/shipments/{accepted_order['id']}/boxes"
    body = {"article": PIPE, "qty": 1, "weight": 1.6}
    problem(customer.post(path, json=body), 403, "forbidden")
    problem(outsider.post(path, json=body), 403, "forbidden")
    problem(sender.post(path, json=body), 403, "forbidden")
    problem(receiver.post(path, json=body), 404, "not-found")
    problem(outsider_stockman.post(path, json=body), 404, "not-found")


def test_box_on_created_or_shipped_order(customer, packer, wh, shipped_order):
    created = flows.create_order(customer, wh[WH3])
    body = {"article": PIPE, "qty": 1, "weight": 1.6}
    problem(packer.post(f"/shipments/{created['id']}/boxes", json=body), 404, "not-found")
    oid = shipped_order["order"]["id"]
    problem(packer.post(f"/shipments/{oid}/boxes", json=body), 409, "invalid-transition")
    problem(packer.delete(f"/shipments/{oid}/boxes/{shipped_order['boxes'][0]['id']}"),
            409, "invalid-transition")


def test_delete_box(accepted_order, packer, receiver, customer):
    oid = accepted_order["id"]
    box = flows.pack(packer, oid, PIPE, 7.5, 12.0)
    problem(customer.delete(f"/shipments/{oid}/boxes/{box['id']}"), 403, "forbidden")
    problem(receiver.delete(f"/shipments/{oid}/boxes/{box['id']}"), 404, "not-found")
    assert packer.delete(f"/shipments/{oid}/boxes/{box['id']}").status_code == 204
    detail = packer.get(f"/shipments/{oid}").json()
    assert detail["boxes"] == []
    assert next(line for line in detail["to_pack"] if line["article"] == PIPE)["packed"] == 0
    problem(packer.delete(f"/shipments/{oid}/boxes/{box['id']}"), 404, "not-found")
    problem(packer.delete(f"/shipments/{oid}/boxes/999999"), 404, "not-found")


def test_delete_box_of_another_order(customer, sender, packer, wh, accepted_order):
    other = flows.accept(sender, flows.create_order(
        customer, wh[WH3], lines=flows.positions((NUT, 5)))["id"])
    box = flows.pack(packer, other["id"], NUT, 5, 0.05)
    problem(packer.delete(f"/shipments/{accepted_order['id']}/boxes/{box['id']}"),
            404, "not-found")
    assert len(packer.get(f"/shipments/{other['id']}").json()["boxes"]) == 1


def test_ship_full(accepted_order, packer, customer, wh, sql):
    oid = accepted_order["id"]
    flows.pack(packer, oid, PIPE, 12.5, 20.0)
    flows.pack(packer, oid, WASHER, 100, 0.5)
    qty_before = flows.balance(sql, PIPE, wh[WH3])[0]

    result = flows.ship(packer, oid)
    assert result["shortage"] == []
    order = result["order"]
    assert order["status"] == "shipped" and order["shipped_at"] and order["version"] == 3
    assert order["accepted_at"] is None
    assert flows.balance(sql, PIPE, wh[WH3]) == (qty_before - Decimal("12.5"), Decimal("0"))
    assert flows.balance(sql, WASHER, wh[WH3])[1] == Decimal("0")
    shipment = flows.movements(sql, PIPE, wh[WH3])[-1]
    assert (shipment.type, shipment.delta, shipment.balance_after, shipment.doc_type,
            shipment.doc_id) == ("shipment", Decimal("-12.5"), qty_before - Decimal("12.5"),
                                 "order", oid)
    assert sql("SELECT status, version, shipped_at IS NOT NULL FROM shipment "
               "WHERE order_id = :id", id=oid) == [("done", 2, True)]
    assert customer.get(f"/orders/{oid}").json()["status"] == "shipped"
    history = customer.get(f"/orders/{oid}/history").json()
    assert [e["status"] for e in history] == ["created", "processing", "shipped"]


def test_partial_shipment_releases_whole_reserve(accepted_order, packer, wh, sql):
    oid = accepted_order["id"]
    flows.pack(packer, oid, PIPE, 7.5, 12.0)
    flows.pack(packer, oid, WASHER, 100, 0.5)
    qty_before, reserved_before = flows.balance(sql, PIPE, wh[WH3])
    assert reserved_before == Decimal("12.5")

    result = flows.ship(packer, oid)
    assert result["shortage"] == [{"article": PIPE, "name": "Труба стальная 32×2",
                                   "ordered": 12.5, "packed": 7.5, "remaining": 5.0}]
    qty_after, reserved_after = flows.balance(sql, PIPE, wh[WH3])
    assert (qty_after, reserved_after) == (qty_before - Decimal("7.5"), Decimal("0"))
    assert qty_after - reserved_after == (qty_before - reserved_before) + Decimal("5")


def test_ship_rules(accepted_order, packer, sender, receiver, customer, outsider_stockman):
    oid = accepted_order["id"]
    path = f"/shipments/{oid}/ship"
    problem(packer.post(path), 428, "precondition-required")
    problem(packer.post(path, headers={"If-Match": '"9"'}), 409, "conflict")
    problem(packer.post(path, headers={"If-Match": '"1"'}), 409, "invalid-transition")
    flows.pack(packer, oid, WASHER, 100, 0.5)
    problem(sender.post(path, headers={"If-Match": '"1"'}), 403, "forbidden")
    problem(customer.post(path, headers={"If-Match": '"1"'}), 403, "forbidden")
    problem(receiver.post(path, headers={"If-Match": '"1"'}), 404, "not-found")
    problem(outsider_stockman.post(path, headers={"If-Match": '"1"'}), 404, "not-found")
    assert packer.post(path, headers={"If-Match": '"1"'}).status_code == 200
    problem(packer.post(path, headers={"If-Match": '"2"'}), 409, "invalid-transition")


def test_ship_uses_shipment_version_not_order_version(accepted_order, packer):
    oid = accepted_order["id"]
    flows.pack(packer, oid, WASHER, 100, 0.5)
    assert accepted_order["version"] == 2
    problem(packer.post(f"/shipments/{oid}/ship", headers={"If-Match": '"2"'}), 409, "conflict")
    resp = packer.post(f"/shipments/{oid}/ship",
                       headers={"If-Match": packer.etag(f"/shipments/{oid}")})
    assert resp.status_code == 200, resp.text


def test_ship_unknown_order(packer):
    problem(packer.post("/shipments/999999/ship", headers={"If-Match": '"1"'}),
            404, "not-found")


def test_shipping_requires_login(anon):
    problem(anon.get("/shipments"), 401, "unauthorized")
