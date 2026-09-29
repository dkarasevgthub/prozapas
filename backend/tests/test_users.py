"""Пользователи и права — api.md §6.7."""
from __future__ import annotations

import pytest

from tests import flows
from tests.flows import MATRIX, WH1, WH3, WH4, problem


def _user(login, warehouse_id, **over) -> dict:
    body = {"full_name": "Новый Сотрудник Тестович", "login": login,
            "email": f"{login}@stalker.ru", "password": "secret123",
            "role": "stockman", "warehouse_id": warehouse_id}
    body.update(over)
    return body


USERS_OPS = [
    ("GET", "/users", {}),
    ("POST", "/users", {"json": {}}),
    ("GET", "/users/1", {}),
    ("PATCH", "/users/1", {"json": {}}),
    ("DELETE", "/users/1", {}),
    ("POST", "/users/1/block", {}),
    ("POST", "/users/1/unblock", {}),
    ("POST", "/users/1/password", {"json": {"password": "secret123"}}),
    ("GET", "/users/1/activity", {}),
    ("GET", "/permissions", {}),
    ("PUT", "/permissions", {"json": []}),
]


@pytest.mark.parametrize("op", USERS_OPS, ids=[f"{m} {p}" for m, p, _ in USERS_OPS])
@pytest.mark.parametrize("who", ["customer", "receiver"])
def test_section_is_closed_to_manager_and_stockman(request, who, op):
    actor = request.getfixturevalue(who)
    method, path, kw = op
    problem(actor.request(method, path, **kw), 403, "forbidden")


def test_list_users(admin, wh):
    body = admin.get("/users").json()
    assert body["total"] == 21 and len(body["items"]) == 21
    names = [u["full_name"] for u in body["items"]]
    assert names == sorted(names)

    def total(**params):
        return admin.get("/users", params=params).json()["total"]

    assert (total(role="stockman"), total(role="manager"), total(role="admin")) == (9, 8, 4)
    assert (total(status="blocked"), total(status="active")) == (2, 19)
    assert total(warehouse_id=wh[WH4]) == 5
    assert (total(q="Соколов"), total(q="соколов"), total(q="p.sok")) == (1, 1, 1)
    assert total(role="nobody") == 0
    assert total(role="stockman", warehouse_id=wh[WH1]) == 4
    page = admin.get("/users", params={"limit": 3, "offset": 20}).json()
    assert len(page["items"]) == 1 and page["limit"] == 3 and page["offset"] == 20


def test_create_user(admin, wh, actor):
    resp = admin.post("/users", json=_user("n.testov", wh[WH3], position="Кладовщик",
                                            phone="+7 900 000-00-00",
                                            hire_date="2026-09-01"))
    assert resp.status_code == 201, resp.text
    user = resp.json()
    assert user["login"] == "n.testov" and user["email"] == "n.testov@stalker.ru"
    assert user["full_name"] == "Новый Сотрудник Тестович" and user["role"] == "stockman"
    assert user["warehouse"]["id"] == wh[WH3] and user["status"] == "active"
    assert user["hire_date"] == "2026-09-01" and user["last_login_at"] is None
    assert user["position"] == "Кладовщик" and user["phone"] == "+7 900 000-00-00"
    assert "password" not in user and "password_hash" not in user
    assert admin.get(f"/users/{user['id']}").json() == user
    assert actor("n.testov", "secret123").id == user["id"]
    assert admin.get("/users").json()["total"] == 22


def test_create_user_minimal(admin, wh):
    resp = admin.post("/users", json=_user("m.minimal", wh[WH1]))
    assert resp.status_code == 201, resp.text
    assert resp.json()["position"] is None and resp.json()["hire_date"] is None


@pytest.mark.parametrize("login, email, title", [
    ("p.sokolov", "fresh@stalker.ru", "Логин занят"),
    ("P.SOKOLOV", "fresh@stalker.ru", "Логин занят"),
    ("fresh", "p.sokolov@stalker.ru", "Почта занята"),
    ("fresh", "P.SOKOLOV@STALKER.RU", "Почта занята"),
])
def test_create_rejects_taken_login_or_email(admin, wh, login, email, title):
    body = problem(admin.post("/users", json=_user(login, wh[WH1], email=email)),
                   409, "conflict")
    assert body["title"] == title


def test_create_rejects_unknown_role_and_warehouse(admin, wh):
    problem(admin.post("/users", json=_user("r.unknown", wh[WH1], role="boss")),
            422, "unprocessable")
    problem(admin.post("/users", json=_user("w.unknown", 999999)), 404, "not-found")


def test_create_rejects_short_password_and_bad_email(admin, wh):
    problem(admin.post("/users", json=_user("p.short", wh[WH1], password="12345")),
            422, "unprocessable")
    resp = admin.post("/users", json=_user("e.bad", wh[WH1], email="not-an-email"))
    assert resp.status_code in (400, 422), resp.text


def test_login_reusable_after_delete(admin, receiver, wh, actor):
    assert admin.delete(f"/users/{receiver.id}").status_code == 204
    resp = admin.post("/users", json=_user("p.sokolov", wh[WH1]))
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] != receiver.id
    assert actor("p.sokolov", "secret123").id == resp.json()["id"]


def test_get_user(admin, receiver):
    assert admin.get(f"/users/{receiver.id}").json()["login"] == "p.sokolov"
    problem(admin.get("/users/999999"), 404, "not-found")
    assert admin.delete(f"/users/{receiver.id}").status_code == 204
    problem(admin.get(f"/users/{receiver.id}"), 404, "not-found")


def test_patch_user_fields(admin, receiver, wh):
    resp = admin.patch(f"/users/{receiver.id}",
                       json={"full_name": "Соколов Пётр Петрович", "email": "petr@stalker.ru",
                             "position": "Старший кладовщик", "phone": None,
                             "warehouse_id": wh[WH3], "role": "manager"})
    assert resp.status_code == 200, resp.text
    user = resp.json()
    assert user["full_name"] == "Соколов Пётр Петрович" and user["email"] == "petr@stalker.ru"
    assert user["position"] == "Старший кладовщик" and user["phone"] is None
    stored = admin.get(f"/users/{receiver.id}").json()
    assert stored["warehouse"]["id"] == wh[WH3] and stored["role"] == "manager"
    assert user == stored, "ответ PATCH должен совпадать с сохранённым состоянием"


def test_patch_without_email_is_allowed(admin, receiver):
    """В UserUpdate нет обязательных полей (openapi.json)."""
    resp = admin.patch(f"/users/{receiver.id}", json={"position": "Приёмщик"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["position"] == "Приёмщик"
    assert resp.json()["email"] == "p.sokolov@stalker.ru"


def test_patch_empty_body_changes_nothing(admin, receiver):
    before = admin.get(f"/users/{receiver.id}").json()
    resp = admin.patch(f"/users/{receiver.id}", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json() == before


def test_patch_rules(admin, receiver, central_admin, wh):
    uid, email = receiver.id, receiver.user["email"]
    problem(admin.patch(f"/users/{uid}", json={"email": "e.morozova@stalker.ru"}),
            409, "conflict")
    problem(admin.patch(f"/users/{uid}", json={"email": "E.MOROZOVA@stalker.ru"}),
            409, "conflict")
    problem(admin.patch(f"/users/{uid}", json={"email": email, "status": "blocked"}),
            422, "unprocessable")
    problem(admin.patch(f"/users/{uid}", json={"email": email, "role": "boss"}),
            422, "unprocessable")
    problem(admin.patch(f"/users/{uid}", json={"email": email, "warehouse_id": 999999}),
            404, "not-found")
    problem(admin.patch(f"/users/{uid}", json={"email": email, "full_name": ""}),
            422, "unprocessable")
    problem(admin.patch(f"/users/{admin.id}",
                        json={"email": admin.user["email"], "role": "manager"}),
            409, "conflict")
    assert admin.patch(f"/users/{admin.id}",
                       json={"email": admin.user["email"], "role": "admin"}).status_code == 200
    problem(admin.patch("/users/999999", json={"email": "x@y.z", "position": "x"}),
            404, "not-found")
    assert admin.patch(f"/users/{uid}", json={"email": email, "status": "active"}).status_code == 200
    assert central_admin.patch(f"/users/{admin.id}",
                               json={"email": admin.user["email"],
                                     "role": "manager"}).status_code == 200


def test_block_and_unblock(admin, receiver, anon, actor):
    uid = receiver.id
    assert admin.post(f"/users/{uid}/block").status_code == 204
    assert admin.get(f"/users/{uid}").json()["status"] == "blocked"
    problem(anon.post("/auth/login", json={"login": "p.sokolov",
                                           "password": flows.SEED_PASSWORD}),
            403, "account-blocked")
    problem(receiver.get("/stock"), 403, "account-blocked")
    problem(anon.post("/auth/refresh", json={"refresh": receiver.refresh}),
            401, "session-expired")
    assert admin.post(f"/users/{uid}/block").status_code == 204
    assert admin.post(f"/users/{uid}/unblock").status_code == 204
    assert admin.get(f"/users/{uid}").json()["status"] == "active"
    assert actor("p.sokolov").get("/stock").status_code == 200
    assert admin.post(f"/users/{uid}/unblock").status_code == 204
    problem(admin.post("/users/999999/block"), 404, "not-found")
    problem(admin.post("/users/999999/unblock"), 404, "not-found")


def test_cannot_block_or_delete_yourself(admin):
    problem(admin.post(f"/users/{admin.id}/block"), 409, "conflict")
    problem(admin.delete(f"/users/{admin.id}"), 409, "conflict")


def test_last_admin_is_protected(admin, customer, sql):
    """Менеджер с правом на пользователей не может убрать последнего администратора."""
    cells = admin.get("/permissions").json()
    for cell in cells:
        if cell["role"] == "manager" and cell["section"] == "users":
            cell["can_view"] = cell["can_edit"] = True
    assert admin.put("/permissions", json=cells).status_code == 200
    others = sql("SELECT u.id FROM user_account u JOIN role r ON r.id = u.role_id "
                 "WHERE r.code = 'admin' AND u.id <> :me", me=admin.id)
    for (uid,) in others:
        assert admin.post(f"/users/{uid}/block").status_code == 204
    problem(customer.post(f"/users/{admin.id}/block"), 409, "conflict")
    problem(customer.delete(f"/users/{admin.id}"), 409, "conflict")
    assert admin.get("/auth/me").status_code == 200


def test_set_password(admin, receiver, anon, actor):
    assert admin.post(f"/users/{receiver.id}/password",
                      json={"password": "new-secret-1"}).status_code == 204
    problem(anon.post("/auth/login", json={"login": "p.sokolov",
                                           "password": flows.SEED_PASSWORD}),
            401, "invalid-credentials")
    assert actor("p.sokolov", "new-secret-1").id == receiver.id
    problem(anon.post("/auth/refresh", json={"refresh": receiver.refresh}),
            401, "session-expired")
    problem(admin.post(f"/users/{receiver.id}/password", json={"password": "12345"}),
            422, "unprocessable")
    problem(admin.post("/users/999999/password", json={"password": "secret123"}),
            404, "not-found")
    assert admin.post(f"/users/{admin.id}/password",
                      json={"password": "admin-new-1"}).status_code == 204


def test_delete_user(admin, customer, anon, wh, sql):
    order = flows.create_order(customer, wh[WH3])
    assert admin.delete(f"/users/{customer.id}").status_code == 204
    problem(anon.post("/auth/login", json={"login": "o.egorova",
                                           "password": flows.SEED_PASSWORD}),
            401, "invalid-credentials")
    problem(customer.get("/auth/me"), 401, "unauthorized")
    problem(admin.get(f"/users/{customer.id}"), 404, "not-found")
    logins = [u["login"] for u in admin.get("/users", params={"limit": 200}).json()["items"]]
    assert "o.egorova" not in logins and len(logins) == 20
    assert admin.get(f"/orders/{order['id']}").json()["responsible"] == {
        "id": customer.id, "name": "Егорова О.Д."}
    assert sql("SELECT deleted_at IS NOT NULL FROM user_account WHERE id = :id",
               id=customer.id) == [(True,)]
    problem(admin.delete(f"/users/{customer.id}"), 404, "not-found")
    problem(admin.delete("/users/999999"), 404, "not-found")


def test_activity_records_admin_actions(admin, receiver, wh):
    created = admin.post("/users", json=_user("a.audit", wh[WH1])).json()
    uid = created["id"]
    assert admin.patch(f"/users/{uid}", json={"position": "x", "email": created["email"]}).status_code == 200
    assert admin.post(f"/users/{uid}/block").status_code == 204
    assert admin.post(f"/users/{uid}/unblock").status_code == 204
    assert admin.post(f"/users/{uid}/password", json={"password": "secret456"}).status_code == 204
    assert admin.delete(f"/users/{uid}").status_code == 204

    body = admin.get(f"/users/{admin.id}/activity").json()
    entries = [(e["entity"], e["entity_id"], e["action"]) for e in body["items"]]
    assert entries[:6] == [("user_account", uid, action) for action in (
        "deleted", "password-changed", "unblocked", "blocked", "updated", "created")]
    assert body["total"] >= 6
    assert all(set(e) == {"id", "entity", "entity_id", "action", "created_at"}
               for e in body["items"])
    page = admin.get(f"/users/{admin.id}/activity", params={"limit": 2, "offset": 1}).json()
    assert [e["id"] for e in page["items"]] == [e["id"] for e in body["items"][1:3]]
    problem(admin.get("/users/999999/activity"), 404, "not-found")
    assert admin.get(f"/users/{receiver.id}/activity").json() == {
        "items": [], "total": 0, "limit": 50, "offset": 0}


def test_activity_covers_order_and_stock_actions(admin, customer, sender, packer, receiver, wh):
    """История действий — из audit_log (api.md §6.7): в ней видны и заказы, и остатки."""
    order = flows.create_order(customer, wh[WH3])
    flows.accept(sender, order["id"])
    flows.pack(packer, order["id"], flows.PIPE, 12.5, 20.0)
    flows.pack(packer, order["id"], flows.WASHER, 100, 0.5)
    flows.ship(packer, order["id"])
    assert receiver.post("/stock/operations",
                         json={"article": flows.BOLT, "warehouse_id": wh[WH1],
                               "type": "receipt", "qty": 1}).status_code == 201

    def actions(actor):
        return {(e["entity"], e["action"])
                for e in admin.get(f"/users/{actor.id}/activity").json()["items"]}

    assert ("order", "accepted") in actions(sender)
    assert ("order", "shipped") in actions(packer)
    assert ("stock_balance", "receipt") in actions(receiver)
    assert ("order", "created") in actions(customer)


def test_permissions_matrix(admin):
    cells = admin.get("/permissions").json()
    assert len(cells) == 18
    assert {(c["role"], c["section"]): (c["can_view"], c["can_edit"]) for c in cells} == {
        (role, section): value
        for role, by_section in MATRIX.items() for section, value in by_section.items()}


def test_put_permissions_replaces_matrix(admin, sender):
    cells = admin.get("/permissions").json()
    for cell in cells:
        if cell["role"] == "manager" and cell["section"] == "catalog":
            cell["can_edit"] = False
    resp = admin.put("/permissions", json=cells)
    assert resp.status_code == 200, resp.text
    assert resp.json() == admin.get("/permissions").json()
    changed = next(c for c in resp.json() if c["role"] == "manager" and c["section"] == "catalog")
    assert changed == {"role": "manager", "section": "catalog", "can_view": True, "can_edit": False}
    problem(sender.post("/catalog", json={"article": "X-300", "name": "x", "unit": "шт."}),
            403, "forbidden")


def _edit_without_view(cells):
    cells[0].update(can_view=False, can_edit=True)


def _duplicate_cell(cells):
    cells.append(dict(cells[0]))


def _unknown_role(cells):
    cells[0]["role"] = "boss"


def _unknown_section(cells):
    cells[0]["section"] = "printing"


def _empty_matrix(cells):
    cells.clear()


def _admin_loses_users_edit(cells):
    for cell in cells:
        if cell["role"] == "admin" and cell["section"] == "users":
            cell["can_edit"] = False


def _admin_users_cell_missing(cells):
    cells[:] = [c for c in cells if not (c["role"] == "admin" and c["section"] == "users")]


@pytest.mark.parametrize("mutate", [
    _edit_without_view, _duplicate_cell, _unknown_role, _unknown_section,
    _empty_matrix, _admin_loses_users_edit, _admin_users_cell_missing,
], ids=lambda f: f.__name__.strip("_"))
def test_put_permissions_rejects_bad_matrix(admin, mutate):
    original = admin.get("/permissions").json()
    cells = [dict(c) for c in original]
    mutate(cells)
    problem(admin.put("/permissions", json=cells), 422, "unprocessable")
    assert admin.get("/permissions").json() == original


def test_put_permissions_partial_matrix_drops_missing_cells(admin, customer):
    """Матрица уходит целиком: пропущенная клетка становится «нет доступа»."""
    cells = [c for c in admin.get("/permissions").json()
             if not (c["role"] == "manager" and c["section"] == "orders")]
    assert admin.put("/permissions", json=cells).status_code == 200
    assert len(admin.get("/permissions").json()) == 17
    problem(customer.get("/orders", params={"tab": "outgoing"}), 403, "forbidden")


def test_users_require_login(anon):
    problem(anon.get("/users"), 401, "unauthorized")
