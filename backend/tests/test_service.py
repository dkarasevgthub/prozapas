"""Служебные операции — api.md §6.1: живость, версия, справочники, сводка."""
from __future__ import annotations

import re

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tests import flows
from tests.flows import MATRIX, SECTIONS, WH1, WH3, problem


def test_health_answers_without_login(anon):
    resp = anon.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_reports_database_outage_as_503(client, app):
    from api.db import get_session

    dead = create_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:1/nowhere")
    previous = app.dependency_overrides[get_session]

    def broken_session():
        with Session(dead) as session:
            yield session

    app.dependency_overrides[get_session] = broken_session
    try:
        problem(client.get(flows.API + "/health"), 503, "unavailable")
    finally:
        app.dependency_overrides[get_session] = previous


def test_version_reports_build_and_schema(anon):
    resp = anon.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == "1.0.0"
    assert re.fullmatch(r"[0-9a-f]{12}", body["schema_revision"]), body


def test_bootstrap_and_dashboard_require_login(anon):
    problem(anon.get("/bootstrap"), 401, "unauthorized")
    problem(anon.get("/dashboard"), 401, "unauthorized")


def test_bootstrap_shape(admin, wh):
    body = admin.get("/bootstrap").json()
    assert [w["code"] for w in body["warehouses"]] == ["128", "129", "130", "131"]
    first = body["warehouses"][0]
    assert first["id"] == wh[WH1]
    assert first["name"] == "Склад №1"
    assert first["owner"] == "ООО «Сталкер Групп»"
    assert first["responsible"]["name"] == "Соколов П.Н."
    assert {r["code"]: r["label"] for r in body["roles"]} == {
        "manager": "Менеджер", "stockman": "Кладовщик", "admin": "Администратор"}
    assert body["sections"] == SECTIONS
    cells = {(p["role"], p["section"]): (p["can_view"], p["can_edit"])
             for p in body["permissions"]}
    assert len(body["permissions"]) == 18
    for role, by_section in MATRIX.items():
        for section, expected in by_section.items():
            assert cells[(role, section)] == expected, (role, section)
    assert body["user"]["login"] == "admin"
    assert body["warehouse"]["id"] == wh[WH1]
    assert len(body["users"]) == 21
    sokolov = next(p for p in body["users"] if p["name"] == "Соколов Пётр Николаевич")
    assert sokolov["warehouse_id"] == wh[WH1]
    assert body["catalog_version"] is None


def test_bootstrap_is_open_to_every_role(receiver, sender):
    for actor in (receiver, sender):
        body = actor.get("/bootstrap").json()
        assert len(body["users"]) == 21
        assert body["user"]["login"] == actor.login


def test_bootstrap_hides_deleted_users(admin, receiver):
    assert admin.delete(f"/users/{receiver.id}").status_code == 204
    names = [p["name"] for p in admin.get("/bootstrap").json()["users"]]
    assert "Соколов Пётр Николаевич" not in names
    assert len(names) == 20


def test_bootstrap_hides_inactive_warehouses(admin, sql, wh):
    sql("UPDATE warehouse SET is_active = false WHERE id = :id", id=wh["131"])
    codes = [w["code"] for w in admin.get("/bootstrap").json()["warehouses"]]
    assert codes == ["128", "129", "130"]


def _counters(actor) -> tuple[dict, list]:
    body = actor.get("/dashboard").json()
    events = body.pop("events")
    return body, events


ZERO = {"in_work": 0, "outgoing_in_work": 0, "incoming_in_work": 0,
        "to_ship": 0, "to_receive": 0}


def test_dashboard_is_empty_without_orders(customer):
    counters, events = _counters(customer)
    assert counters == ZERO
    assert events == []


def test_dashboard_follows_the_order_lifecycle(customer, sender, packer, receiver,
                                               outsider, wh):
    order = flows.create_order(customer, wh[WH3])
    oid = order["id"]

    counters, events = _counters(customer)
    assert counters == {**ZERO, "in_work": 1, "outgoing_in_work": 1}
    assert len(events) == 1
    event = events[0]
    assert event["order_id"] == oid
    assert event["number"] == order["number"]
    assert event["status"] == "created"
    assert event["reason"] is None
    assert event["user"] == {"id": customer.id, "name": "Егорова О.Д."}
    assert event["occurred_at"]

    counters, events = _counters(sender)
    assert counters == {**ZERO, "in_work": 1, "incoming_in_work": 1, "to_ship": 1}
    assert len(events) == 1

    counters, events = _counters(outsider)
    assert counters == ZERO
    assert events == []

    flows.accept(sender, oid)
    counters, events = _counters(sender)
    assert counters == {**ZERO, "in_work": 1, "incoming_in_work": 1, "to_ship": 1}
    assert {e["status"] for e in events} == {"created", "processing"}

    flows.pack(packer, oid, flows.PIPE, 12.5, 20.0)
    flows.pack(packer, oid, flows.WASHER, 100, 0.5)
    flows.ship(packer, oid)
    counters, _ = _counters(customer)
    assert counters == {**ZERO, "to_receive": 1}
    counters, _ = _counters(sender)
    assert counters == ZERO

    for box in receiver.get(f"/receipts/{oid}").json()["boxes"]:
        flows.receive(receiver, oid, box["barcode"], box["weight"])
    flows.complete(receiver, oid)
    counters, events = _counters(customer)
    assert counters == ZERO
    assert {e["status"] for e in events} == {"created", "processing", "shipped", "received"}
    assert len(events) == 4


def test_dashboard_shows_at_most_eight_events(customer, wh):
    for _ in range(9):
        flows.create_order(customer, wh[WH3])
    _, events = _counters(customer)
    assert len(events) == 8


def test_dashboard_without_warehouse_sees_nothing(customer, sql, actor, wh):
    flows.create_order(customer, wh[WH3])
    sql("UPDATE user_account SET warehouse_id = NULL WHERE login = 'm.kiselev'")
    homeless = actor("m.kiselev")
    assert homeless.payload["warehouse"] is None
    counters, events = _counters(homeless)
    assert counters == ZERO
    assert events == []
