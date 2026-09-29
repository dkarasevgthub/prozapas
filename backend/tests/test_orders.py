"""Заказы — api.md §6.2, §7.1, §8.3."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests import flows
from tests.flows import BELT, BOLT, PIPE, WASHER, WH1, WH3, WH4, WHC, problem


def _create(actor, warehouse_id, lines=None, key=None, comment=None):
    body = {"from_warehouse_id": warehouse_id,
            "positions": (lines if lines is not None
                          else flows.positions((PIPE, 12.5), (WASHER, 100)))}
    if comment is not None:
        body["comment"] = comment
    headers = {"Idempotency-Key": key} if key is not None else {}
    return actor.post("/orders", json=body, headers=headers)


def _by_article(order_positions):
    return sorted(order_positions, key=lambda p: p["article"])


# --- Создание --------------------------------------------------------------------

def test_create_order(customer, wh):
    resp = _create(customer, wh[WH3], key="k-1", comment="срочно")
    assert resp.status_code == 201, resp.text
    order = resp.json()
    assert order["number"].isdigit() and int(order["number"]) >= 2001
    assert order["status"] == "created"
    assert order["from_warehouse"]["id"] == wh[WH3]
    assert order["from_warehouse"]["responsible"]["name"] == "Морозова Е.В."
    assert order["to_warehouse"]["id"] == wh[WH1]
    assert order["responsible"] == {"id": customer.id, "name": "Егорова О.Д."}
    assert order["comment"] == "срочно" and order["reason"] is None
    assert _by_article(order["positions"]) == [
        {"article": PIPE, "name": "Труба стальная 32×2", "unit": "м", "qty": 12.5},
        {"article": WASHER, "name": "Шайба М8 ГОСТ 6402", "unit": "шт.", "qty": 100.0}]
    assert order["shipped_at"] is None and order["accepted_at"] is None
    assert order["version"] == 1 and order["created_at"]


def test_order_numbers_grow(customer, wh):
    first = flows.create_order(customer, wh[WH3])
    second = flows.create_order(customer, wh[WH3])
    assert int(second["number"]) > int(first["number"])
    assert second["id"] > first["id"]


def test_create_requires_idempotency_key(customer, wh):
    problem(_create(customer, wh[WH3]), 400, "bad-request")
    problem(_create(customer, wh[WH3], key="   "), 400, "bad-request")


def test_same_key_and_body_returns_the_same_order(customer, wh):
    first = _create(customer, wh[WH3], key="same")
    again = _create(customer, wh[WH3], key="same")
    assert first.status_code == 201 and again.status_code in (200, 201)
    assert again.json()["id"] == first.json()["id"]
    assert customer.get("/orders", params={"tab": "outgoing"}).json()["total"] == 1


def test_same_key_with_other_body_is_conflict(customer, wh):
    assert _create(customer, wh[WH3], key="reused").status_code == 201
    problem(_create(customer, wh[WH3], lines=flows.positions((PIPE, 1)), key="reused"),
            409, "conflict")


def test_different_keys_create_different_orders(customer, wh):
    first = _create(customer, wh[WH3], key="a").json()
    second = _create(customer, wh[WH3], key="b").json()
    assert first["id"] != second["id"]


def test_idempotency_key_does_not_leak_orders_between_users(customer, outsider, wh):
    """Ключ склада №1 в руках склада №4 не должен отдавать чужой заказ (api.md §3.1)."""
    mine = _create(customer, wh[WH3], key="shared-key").json()
    resp = _create(outsider, wh[WH3], key="shared-key")
    assert resp.status_code in (404, 409) \
        or resp.json()["to_warehouse"]["id"] == outsider.warehouse_id, resp.json()
    assert resp.status_code != 201 or resp.json()["id"] != mine["id"]


@pytest.mark.parametrize("lines", [
    pytest.param([], id="пустые позиции"),
    pytest.param(flows.positions((PIPE, 0)), id="нулевое количество"),
    pytest.param(flows.positions((PIPE, -1)), id="отрицательное количество"),
    pytest.param(flows.positions((PIPE, 1), (PIPE, 2)), id="артикул повторяется"),
    pytest.param(flows.positions(("no-such", 1)), id="артикула нет"),
])
def test_create_rejects_bad_positions(customer, wh, lines):
    problem(_create(customer, wh[WH3], lines=lines, key=str(uuid.uuid4())),
            422, "unprocessable")
    assert customer.get("/orders", params={"tab": "outgoing"}).json()["total"] == 0


def test_create_rejects_archived_article(customer, sender, wh, sql):
    item_id = sql("SELECT id FROM catalog_item WHERE article = :a", a=BELT)[0][0]
    assert sender.post(f"/catalog/{item_id}/archive").status_code == 204
    problem(_create(customer, wh[WH3], lines=flows.positions((BELT, 1)), key="archived"),
            422, "unprocessable")


def test_create_rejects_own_warehouse(customer, wh):
    problem(_create(customer, wh[WH1], key="own"), 422, "unprocessable")


def test_create_rejects_unknown_warehouse(customer):
    problem(_create(customer, 999999, key="no-wh"), 404, "not-found")


def test_create_rejects_inactive_warehouse(customer, wh, sql):
    sql("UPDATE warehouse SET is_active = false WHERE id = :id", id=wh[WH3])
    problem(_create(customer, wh[WH3], key="inactive"), 422, "unprocessable")


def test_create_rejects_deleted_warehouse(customer, wh, sql):
    sql("UPDATE warehouse SET deleted_at = now() WHERE id = :id", id=wh[WH3])
    problem(_create(customer, wh[WH3], key="deleted"), 404, "not-found")


def test_create_needs_a_warehouse_of_your_own(actor, sql, wh):
    sql("UPDATE user_account SET warehouse_id = NULL WHERE login = 'o.egorova'")
    homeless = actor("o.egorova")
    problem(_create(homeless, wh[WH3], key="homeless"), 422, "unprocessable")


def test_create_does_not_check_availability(customer, wh):
    """Наличие на чужом складе при создании не проверяется (api.md §6.2)."""
    resp = _create(customer, wh[WH3], lines=flows.positions((BELT, 999999)), key="big")
    assert resp.status_code == 201, resp.text


def test_stockman_cannot_create_orders(receiver, wh):
    problem(_create(receiver, wh[WH3], key="stockman"), 403, "forbidden")


def test_admin_can_create_orders(admin, wh):
    assert _create(admin, wh[WH3], key="admin").status_code == 201


# --- Список, карточка, история --------------------------------------------------

def test_list_shows_one_order_from_two_sides(customer, sender, outsider, wh):
    order = flows.create_order(customer, wh[WH3])
    outgoing = customer.get("/orders", params={"tab": "outgoing"}).json()
    assert outgoing["total"] == 1
    row = outgoing["items"][0]
    assert row["id"] == order["id"] and row["number"] == order["number"]
    assert row["status"] == "created" and row["positions_count"] == 2
    assert row["counterparty"]["id"] == wh[WH3]
    assert row["counterparty"]["responsible"]["name"] == "Морозова Е.В."
    assert row["responsible"] == {"id": customer.id, "name": "Егорова О.Д."}
    assert row["shipped_at"] is None and row["accepted_at"] is None and row["created_at"]
    assert customer.get("/orders", params={"tab": "incoming"}).json()["total"] == 0

    incoming = sender.get("/orders", params={"tab": "incoming"}).json()
    assert [r["id"] for r in incoming["items"]] == [order["id"]]
    assert incoming["items"][0]["counterparty"]["id"] == wh[WH1]
    assert sender.get("/orders", params={"tab": "outgoing"}).json()["total"] == 0

    for tab in ("outgoing", "incoming"):
        assert outsider.get("/orders", params={"tab": tab}).json()["items"] == []
    problem(outsider.get(f"/orders/{order['id']}"), 404, "not-found")
    problem(outsider.get(f"/orders/{order['id']}/history"), 404, "not-found")


def test_list_newest_first_and_paginated(customer, wh):
    ids = [flows.create_order(customer, wh[WH3])["id"] for _ in range(3)]
    body = customer.get("/orders", params={"tab": "outgoing", "limit": 2}).json()
    assert (body["total"], body["limit"], body["offset"]) == (3, 2, 0)
    assert [r["id"] for r in body["items"]] == ids[::-1][:2]
    rest = customer.get("/orders", params={"tab": "outgoing", "limit": 2, "offset": 2}).json()
    assert [r["id"] for r in rest["items"]] == [ids[0]]


def test_list_filters_by_status(customer, sender, wh):
    created = flows.create_order(customer, wh[WH3])
    processing = flows.create_order(customer, wh[WH3])
    flows.accept(sender, processing["id"])
    declined = flows.create_order(customer, wh[WH3])
    flows.decline(sender, declined["id"], "нет")

    def ids(**params):
        return {r["id"] for r in customer.get(
            "/orders", params={"tab": "outgoing", **params}).json()["items"]}

    assert ids(status="created") == {created["id"]}
    assert ids(status=["created", "processing"]) == {created["id"], processing["id"]}
    assert ids(status="declined") == {declined["id"]}
    assert ids(status="shipped") == set()
    assert ids() == {created["id"], processing["id"], declined["id"]}


def test_list_filters_by_counterparty_and_responsible(customer, admin, wh):
    from_wh3 = flows.create_order(customer, wh[WH3])
    from_central = flows.create_order(customer, wh[WHC],
                                      lines=flows.positions((flows.PUMP, 1)))
    by_admin = flows.create_order(admin, wh[WH3])

    def ids(**params):
        return {r["id"] for r in customer.get(
            "/orders", params={"tab": "outgoing", **params}).json()["items"]}

    assert ids(warehouse_id=wh[WH3]) == {from_wh3["id"], by_admin["id"]}
    assert ids(warehouse_id=wh[WHC]) == {from_central["id"]}
    assert ids(warehouse_id=wh[WH4]) == set()
    assert ids(responsible_id=customer.id) == {from_wh3["id"], from_central["id"]}
    assert ids(responsible_id=admin.id) == {by_admin["id"]}


def test_list_filters_by_number_prefix_and_dates(customer, wh):
    order = flows.create_order(customer, wh[WH3])
    today = date.today()

    def total(**params):
        return customer.get("/orders", params={"tab": "outgoing", **params}).json()["total"]

    assert total(q=order["number"][:3]) == 1
    assert total(q=order["number"]) == 1
    assert total(q="9" * 9) == 0
    assert total(created_from=(today - timedelta(days=1)).isoformat()) == 1
    assert total(created_from=(today + timedelta(days=2)).isoformat()) == 0
    assert total(created_to=(today + timedelta(days=1)).isoformat()) == 1
    assert total(created_to=(today - timedelta(days=2)).isoformat()) == 0


def test_card_carries_etag_that_follows_version(customer, sender, wh):
    order = flows.create_order(customer, wh[WH3])
    resp = customer.get(f"/orders/{order['id']}")
    assert resp.status_code == 200 and resp.headers["ETag"] == '"1"'
    assert resp.json() == order
    flows.accept(sender, order["id"])
    resp = sender.get(f"/orders/{order['id']}")
    assert resp.headers["ETag"] == '"2"'
    assert resp.json()["version"] == 2 and resp.json()["status"] == "processing"


def test_card_unknown_order(customer):
    problem(customer.get("/orders/999999"), 404, "not-found")
    problem(customer.get("/orders/999999/history"), 404, "not-found")


def test_history_lists_status_events(customer, sender, wh):
    order = flows.create_order(customer, wh[WH3])
    flows.decline(sender, order["id"], "нет остатка")
    events = customer.get(f"/orders/{order['id']}/history").json()
    assert [e["status"] for e in events] == ["created", "declined"]
    assert events[0]["user"] == {"id": customer.id, "name": "Егорова О.Д."}
    assert events[0]["reason"] is None
    assert events[1]["user"] == {"id": sender.id, "name": "Морозова Е.В."}
    assert events[1]["reason"] == "нет остатка"
    assert all(e["order_id"] == order["id"] and e["number"] == order["number"]
               and e["occurred_at"] for e in events)


# --- Принять ---------------------------------------------------------------------

def test_accept_reserves_stock_and_opens_documents(customer, sender, wh, sql):
    order = flows.create_order(customer, wh[WH3])
    pipe_before = flows.balance(sql, PIPE, wh[WH3])
    washer_before = flows.balance(sql, WASHER, wh[WH3])

    accepted = flows.accept(sender, order["id"])
    assert accepted["status"] == "processing" and accepted["version"] == 2
    assert accepted["accepted_at"] is None
    assert flows.balance(sql, PIPE, wh[WH3]) == (pipe_before[0], pipe_before[1] + Decimal("12.5"))
    assert flows.balance(sql, WASHER, wh[WH3]) == (washer_before[0],
                                                   washer_before[1] + Decimal("100"))
    reserve = flows.movements(sql, PIPE, wh[WH3])[-1]
    assert (reserve.type, reserve.delta, reserve.balance_after, reserve.doc_type,
            reserve.doc_id, reserve.user_id) == (
        "reserve", Decimal("12.5"), pipe_before[0], "order", order["id"], sender.id)
    assert sql("SELECT status, version FROM shipment WHERE order_id = :id",
               id=order["id"]) == [("progress", 1)]
    assert sql("SELECT status, warehouse_id FROM receipt WHERE order_id = :id",
               id=order["id"]) == [("progress", wh[WH1])]
    history = customer.get(f"/orders/{order['id']}/history").json()
    assert [e["status"] for e in history] == ["created", "processing"]


def test_accept_needs_if_match(sender, customer, wh):
    order = flows.create_order(customer, wh[WH3])
    path = f"/orders/{order['id']}/accept"
    problem(sender.post(path), 428, "precondition-required")
    problem(sender.post(path, headers={"If-Match": ""}), 428, "precondition-required")
    problem(sender.post(path, headers={"If-Match": "abc"}), 400, "bad-request")
    problem(sender.post(path, headers={"If-Match": '"5"'}), 409, "conflict")
    assert sender.get(f"/orders/{order['id']}").json()["status"] == "created"


def test_accept_takes_weak_etag(sender, customer, wh):
    order = flows.create_order(customer, wh[WH3])
    resp = sender.post(f"/orders/{order['id']}/accept", headers={"If-Match": 'W/"1"'})
    assert resp.status_code == 200, resp.text


def test_accept_only_by_sender_and_only_once(customer, sender, outsider, wh):
    order = flows.create_order(customer, wh[WH3])
    path = f"/orders/{order['id']}/accept"
    problem(customer.post(path, headers={"If-Match": '"1"'}), 409, "invalid-transition")
    problem(outsider.post(path, headers={"If-Match": '"1"'}), 404, "not-found")
    flows.accept(sender, order["id"])
    problem(sender.post(path, headers={"If-Match": '"2"'}), 409, "invalid-transition")


def test_accept_needs_orders_edit(packer, customer, wh):
    order = flows.create_order(customer, wh[WH3])
    problem(packer.post(f"/orders/{order['id']}/accept", headers={"If-Match": '"1"'}),
            403, "forbidden")


def test_accept_by_admin_of_sender_warehouse(actor, customer, wh):
    order = flows.create_order(customer, wh[WH3])
    assert flows.accept(actor("a.pavlov"), order["id"])["status"] == "processing"


def test_accept_with_insufficient_stock_changes_nothing(customer, sender, wh, sql):
    order = flows.create_order(customer, wh[WH3], lines=flows.positions((BELT, 30), (PIPE, 1)))
    belt_before = flows.balance(sql, BELT, wh[WH3])
    body = problem(sender.post(f"/orders/{order['id']}/accept",
                               headers={"If-Match": '"1"'}), 409, "insufficient-stock")
    assert body["positions"] == [{"article": BELT, "requested": 30.0, "available": 25.0}]
    assert flows.balance(sql, BELT, wh[WH3]) == belt_before
    assert flows.balance(sql, PIPE, wh[WH3])[1] == Decimal("0")
    assert sender.get(f"/orders/{order['id']}").json() == order
    assert sql("SELECT count(*) FROM shipment WHERE order_id = :id", id=order["id"])[0][0] == 0
    assert flows.movements(sql, BELT, wh[WH3]) == []


def test_accept_counts_reserved_stock_as_unavailable(customer, sender, wh):
    first = flows.create_order(customer, wh[WH3], lines=flows.positions((BELT, 20)))
    flows.accept(sender, first["id"])
    second = flows.create_order(customer, wh[WH3], lines=flows.positions((BELT, 10)))
    body = problem(sender.post(f"/orders/{second['id']}/accept",
                               headers={"If-Match": '"1"'}), 409, "insufficient-stock")
    assert body["positions"] == [{"article": BELT, "requested": 10.0, "available": 5.0}]


def test_accept_item_never_stocked_at_sender(customer, sender, wh):
    order = flows.create_order(customer, wh[WH3], lines=flows.positions((BOLT, 1)))
    body = problem(sender.post(f"/orders/{order['id']}/accept",
                               headers={"If-Match": '"1"'}), 409, "insufficient-stock")
    assert body["positions"] == [{"article": BOLT, "requested": 1.0, "available": 0.0}]


# --- Отклонить и отменить -------------------------------------------------------

def test_decline_records_reason(customer, sender, wh):
    order = flows.create_order(customer, wh[WH3])
    declined = flows.decline(sender, order["id"], "нет на складе")
    assert declined["status"] == "declined" and declined["reason"] == "нет на складе"
    assert declined["version"] == 2
    assert customer.get(f"/orders/{order['id']}").json()["reason"] == "нет на складе"
    assert customer.get("/orders", params={"tab": "outgoing", "status": "declined"}
                        ).json()["total"] == 1


def test_decline_without_reason(customer, sender, wh):
    order = flows.create_order(customer, wh[WH3])
    assert flows.decline(sender, order["id"])["reason"] is None


def test_decline_rules(customer, sender, outsider, wh):
    order = flows.create_order(customer, wh[WH3])
    path = f"/orders/{order['id']}/decline"
    problem(sender.post(path, json={}), 428, "precondition-required")
    problem(customer.post(path, json={}, headers={"If-Match": '"1"'}), 409, "invalid-transition")
    problem(outsider.post(path, json={}, headers={"If-Match": '"1"'}), 404, "not-found")
    flows.accept(sender, order["id"])
    problem(sender.post(path, json={}, headers={"If-Match": '"2"'}), 409, "invalid-transition")


def test_cancel_created_order(customer, sender, wh):
    order = flows.create_order(customer, wh[WH3])
    cancelled = flows.cancel(customer, order["id"], "передумали")
    assert cancelled["status"] == "cancelled" and cancelled["reason"] == "передумали"
    assert cancelled["version"] == 2
    history = customer.get(f"/orders/{order['id']}/history").json()
    assert [e["status"] for e in history] == ["created", "cancelled"]
    assert history[1]["reason"] == "передумали"


def test_cancel_processing_order_releases_reserve_and_documents(customer, sender, wh, sql):
    order = flows.create_order(customer, wh[WH3])
    flows.accept(sender, order["id"])
    qty_before, reserved_before = flows.balance(sql, PIPE, wh[WH3])
    assert reserved_before == Decimal("12.5")

    cancelled = flows.cancel(customer, order["id"], "не нужно")
    assert cancelled["status"] == "cancelled" and cancelled["version"] == 3
    assert flows.balance(sql, PIPE, wh[WH3]) == (qty_before, Decimal("0"))
    assert flows.balance(sql, WASHER, wh[WH3])[1] == Decimal("0")
    release = flows.movements(sql, PIPE, wh[WH3])[-1]
    assert (release.type, release.delta, release.balance_after, release.doc_id) == (
        "unreserve", Decimal("-12.5"), qty_before, order["id"])
    assert sql("SELECT count(*) FROM shipment WHERE order_id = :id", id=order["id"])[0][0] == 0
    assert sql("SELECT count(*) FROM receipt WHERE order_id = :id", id=order["id"])[0][0] == 0
    assert sender.get("/shipments").json()["total"] == 0


def test_cancel_rules(customer, sender, packer, outsider, wh):
    order = flows.create_order(customer, wh[WH3])
    path = f"/orders/{order['id']}/cancel"
    problem(customer.post(path, json={}), 428, "precondition-required")
    problem(sender.post(path, json={}, headers={"If-Match": '"1"'}), 409, "invalid-transition")
    problem(outsider.post(path, json={}, headers={"If-Match": '"1"'}), 404, "not-found")
    problem(customer.post(path, json={}, headers={"If-Match": '"7"'}), 409, "conflict")
    flows.accept(sender, order["id"])
    flows.pack(packer, order["id"], PIPE, 1, 1.6)
    problem(customer.post(path, json={}, headers={"If-Match": '"2"'}), 409, "invalid-transition")
    assert customer.get(f"/orders/{order['id']}").json()["status"] == "processing"


def test_cancel_after_shipping_or_decline_is_rejected(customer, sender, wh, shipped_order):
    shipped = shipped_order["order"]
    problem(customer.post(f"/orders/{shipped['id']}/cancel", json={},
                          headers={"If-Match": f'"{shipped["version"]}"'}),
            409, "invalid-transition")
    declined = flows.create_order(customer, wh[WH3])
    flows.decline(sender, declined["id"])
    problem(customer.post(f"/orders/{declined['id']}/cancel", json={},
                          headers={"If-Match": '"2"'}), 409, "invalid-transition")


def test_cancel_needs_orders_edit(receiver, customer, wh):
    order = flows.create_order(customer, wh[WH3])
    problem(receiver.post(f"/orders/{order['id']}/cancel", json={},
                          headers={"If-Match": '"1"'}), 403, "forbidden")


def test_fractional_quantities_are_exact(customer, sender, wh, sql):
    order = flows.create_order(customer, wh[WH3], lines=flows.positions((PIPE, 12.345)))
    assert order["positions"][0]["qty"] == 12.345
    flows.accept(sender, order["id"])
    assert flows.balance(sql, PIPE, wh[WH3])[1] == Decimal("12.345")


def test_orders_require_login(anon):
    problem(anon.get("/orders", params={"tab": "outgoing"}), 401, "unauthorized")
