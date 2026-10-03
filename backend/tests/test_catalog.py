"""Справочник — api.md §6.6."""
from __future__ import annotations

import pytest

from tests import flows
from tests.flows import PIPE, WH3, problem


def _item(article="X-100", **over) -> dict:
    body = {"article": article, "code1c": f"1C-{article}", "name": f"Тест {article}",
            "unit": "шт.", "unit_weight": 0.25}
    body.update(over)
    return body


def test_list_shows_active_items_sorted_by_name(sender):
    body = sender.get("/catalog").json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert (body["total"], body["limit"], body["offset"]) == (22, 50, 0)
    names = [i["name"] for i in body["items"]]
    assert names == sorted(names)
    assert all(not i["is_archived"] for i in body["items"])
    pipe = next(i for i in body["items"] if i["article"] == PIPE)
    assert pipe == {"id": pipe["id"], "article": "100512", "code1c": "ТМЦ-00512",
                    "name": "Труба стальная 32×2", "unit": "м", "unit_weight": 1.6,
                    "is_archived": False}


def test_list_pagination(sender):
    page = sender.get("/catalog", params={"limit": 5}).json()
    assert len(page["items"]) == 5 and page["total"] == 22 and page["limit"] == 5
    tail = sender.get("/catalog", params={"limit": 5, "offset": 20}).json()
    assert len(tail["items"]) == 2 and tail["offset"] == 20
    assert {i["id"] for i in page["items"]}.isdisjoint({i["id"] for i in tail["items"]})
    assert len(sender.get("/catalog", params={"limit": 200}).json()["items"]) == 22


@pytest.mark.parametrize("q, article", [
    ("труба", "100512"), ("Труба стальная", "100512"), ("ТРУБА", "100512"),
    ("100512", "100512"), ("1005", "100512"), ("ТМЦ-00512", "100512"),
    ("подшипник", "201187"),
])
def test_search_by_name_article_and_code1c(sender, q, article):
    found = [i["article"] for i in sender.get("/catalog", params={"q": q}).json()["items"]]
    assert article in found, found


def test_search_miss_returns_empty_page(sender):
    body = sender.get("/catalog", params={"q": "несуществующее"}).json()
    assert body["items"] == [] and body["total"] == 0


def test_create_item(sender):
    resp = sender.post("/catalog", json=_item("X-100", unit_weight=0.005))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["article"] == "X-100" and body["code1c"] == "1C-X-100"
    assert body["name"] == "Тест X-100" and body["unit"] == "шт."
    assert body["unit_weight"] == 0.005
    assert body["is_archived"] is False
    assert sender.get(f"/catalog/{body['id']}").json() == body


def test_create_without_code1c_and_weight(sender):
    resp = sender.post("/catalog", json={"article": "X-101", "name": "Без кода", "unit": "шт."})
    assert resp.status_code == 201, resp.text
    assert resp.json()["code1c"] is None and resp.json()["unit_weight"] == 0


def test_create_rejects_taken_article(sender):
    body = problem(sender.post("/catalog", json=_item(PIPE)), 409, "conflict")
    assert body["title"] == "Артикул занят"


def test_create_rejects_taken_code1c(sender):
    body = problem(sender.post("/catalog", json=_item("X-102", code1c="ТМЦ-00512")),
                   409, "conflict")
    assert body["title"] == "Код 1С занят"


def test_create_rejects_article_of_archived_item(sender):
    item = sender.post("/catalog", json=_item("X-103")).json()
    assert sender.post(f"/catalog/{item['id']}/archive").status_code == 204
    problem(sender.post("/catalog", json=_item("X-103")), 409, "conflict")


def test_create_rejects_negative_weight(sender):
    problem(sender.post("/catalog", json=_item("X-104", unit_weight=-1)),
            422, "unprocessable")


def test_stockman_reads_but_does_not_edit(receiver):
    assert receiver.get("/catalog").status_code == 200
    problem(receiver.post("/catalog", json=_item("X-105")), 403, "forbidden")
    problem(receiver.patch("/catalog/1", json={"name": "x"}), 403, "forbidden")
    problem(receiver.post("/catalog/1/archive"), 403, "forbidden")


def test_get_unknown_item_is_404(sender):
    problem(sender.get("/catalog/999999"), 404, "not-found")


def test_patch_fields(sender):
    item = sender.post("/catalog", json=_item("X-106")).json()
    resp = sender.patch(f"/catalog/{item['id']}",
                        json={"name": "Новое имя", "unit": "м", "unit_weight": 0.333,
                              "code1c": "1C-NEW"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {**item, "name": "Новое имя", "unit": "м",
                           "unit_weight": 0.333, "code1c": "1C-NEW"}


def test_patch_clears_code1c_with_explicit_null(sender):
    item = sender.post("/catalog", json=_item("X-107")).json()
    resp = sender.patch(f"/catalog/{item['id']}", json={"code1c": None})
    assert resp.status_code == 200 and resp.json()["code1c"] is None


def test_patch_empty_body_changes_nothing(sender):
    item = sender.post("/catalog", json=_item("X-108")).json()
    assert sender.patch(f"/catalog/{item['id']}", json={}).json() == item


def test_patch_rejects_taken_code1c(sender):
    item = sender.post("/catalog", json=_item("X-109")).json()
    problem(sender.patch(f"/catalog/{item['id']}", json={"code1c": "ТМЦ-00512"}),
            409, "conflict")


@pytest.mark.parametrize("field", ["name", "unit", "unit_weight"])
def test_patch_rejects_null_in_required_field(sender, field):
    item = sender.post("/catalog", json=_item("X-110")).json()
    problem(sender.patch(f"/catalog/{item['id']}", json={field: None}), 422, "unprocessable")


def test_patch_rejects_empty_name(sender):
    item = sender.post("/catalog", json=_item("X-111")).json()
    problem(sender.patch(f"/catalog/{item['id']}", json={"name": ""}), 422, "unprocessable")


def test_patch_unknown_item(sender):
    problem(sender.patch("/catalog/999999", json={"name": "x"}), 404, "not-found")


def test_archive_hides_item_from_list_but_keeps_card(sender):
    item = sender.post("/catalog", json=_item("X-112")).json()
    assert sender.post(f"/catalog/{item['id']}/archive").status_code == 204
    active = sender.get("/catalog", params={"limit": 200}).json()["items"]
    assert item["id"] not in [i["id"] for i in active]
    archived = sender.get("/catalog", params={"archived": True}).json()["items"]
    assert [i["id"] for i in archived] == [item["id"]]
    assert sender.get(f"/catalog/{item['id']}").json()["is_archived"] is True


def test_archive_is_idempotent(sender):
    item = sender.post("/catalog", json=_item("X-113")).json()
    assert sender.post(f"/catalog/{item['id']}/archive").status_code == 204
    assert sender.post(f"/catalog/{item['id']}/archive").status_code == 204


def test_archive_unknown_item(sender):
    problem(sender.post("/catalog/999999/archive"), 404, "not-found")


def test_archived_item_stays_in_stock_and_history_but_leaves_new_orders(
        sender, customer, packer, wh, sql):
    item_id = sql("SELECT id FROM catalog_item WHERE article = :a", a=PIPE)[0][0]
    resp = packer.post("/stock/operations",
                       json={"article": PIPE, "warehouse_id": wh[WH3],
                             "type": "writeoff", "qty": 0.5, "comment": "до архива"})
    assert resp.status_code == 201, resp.text
    assert sender.post(f"/catalog/{item_id}/archive").status_code == 204

    resp = customer.post("/orders",
                         json={"from_warehouse_id": wh[WH3],
                               "positions": flows.positions((PIPE, 1))},
                         headers={"Idempotency-Key": "archived-article"})
    problem(resp, 422, "unprocessable")

    stock = packer.get("/stock", params={"q": PIPE}).json()["items"]
    assert [i["article"] for i in stock] == [PIPE]
    history = packer.get(f"/stock/{item_id}/movements").json()
    assert history["total"] == 1 and history["items"][0]["comment"] == "до архива"
    problem(packer.post("/stock/operations",
                        json={"article": PIPE, "warehouse_id": wh[WH3],
                              "type": "receipt", "qty": 1}), 422, "unprocessable")


def test_unit_weight_keeps_three_decimals(sender):
    item = sender.post("/catalog", json=_item("X-114", unit_weight=0.005)).json()
    assert sender.get(f"/catalog/{item['id']}").json()["unit_weight"] == 0.005


def test_catalog_requires_login(anon):
    problem(anon.get("/catalog"), 401, "unauthorized")
