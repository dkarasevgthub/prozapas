"""Матрица прав — api.md §5: роль × раздел, два уровня, действует сразу."""
from __future__ import annotations

import copy

import pytest

from tests import flows
from tests.flows import MATRIX, problem

OWN_WAREHOUSE = "<own warehouse>"

#: Представительный запрос на каждый раздел и уровень. При разрешении он
#: доходит до домена и отвечает 404 (объекта нет), при запрете — 403.
CASES = [
    ("orders", "view", "GET", "/orders", {"params": {"tab": "outgoing"}}),
    ("orders", "edit", "POST", "/orders/999999/accept", {"headers": {"If-Match": '"1"'}}),
    ("shipping", "view", "GET", "/shipments", {}),
    ("shipping", "edit", "POST", "/shipments/999999/ship", {"headers": {"If-Match": '"1"'}}),
    ("receiving", "view", "GET", "/receipts", {}),
    ("receiving", "edit", "POST", "/receipts/999999/complete",
     {"headers": {"If-Match": '"1"'}}),
    ("catalog", "view", "GET", "/catalog", {}),
    ("catalog", "edit", "POST", "/catalog/999999/archive", {}),
    ("stock", "view", "GET", "/stock/summary", {}),
    ("stock", "edit", "POST", "/stock/operations",
     {"json": {"article": "no-such-article", "warehouse_id": OWN_WAREHOUSE,
               "type": "receipt", "qty": 1}}),
    ("users", "view", "GET", "/users", {}),
    ("users", "edit", "POST", "/users/999999/block", {}),
]
ROLES = {"manager": "o.egorova", "stockman": "p.sokolov", "admin": "admin"}


def _request(actor, case):
    _section, _level, method, path, kw = case
    kw = copy.deepcopy(kw)
    if kw.get("json", {}).get("warehouse_id") == OWN_WAREHOUSE:
        kw["json"]["warehouse_id"] = actor.warehouse_id
    return actor.request(method, path, **kw)


@pytest.mark.parametrize("role", list(ROLES))
@pytest.mark.parametrize("case", CASES, ids=[f"{c[0]}-{c[1]}" for c in CASES])
def test_matrix_is_enforced_on_every_section(actor, role, case):
    section, level = case[0], case[1]
    resp = _request(actor(ROLES[role]), case)
    allowed = MATRIX[role][section][0 if level == "view" else 1]
    if allowed:
        assert resp.status_code not in (401, 403, 500), \
            f"{role} {section} {level}: {resp.status_code} {resp.text}"
    else:
        body = problem(resp, 403, "forbidden")
        assert body["title"] == "Недостаточно прав"


def test_removed_permission_applies_on_next_request(admin, packer):
    assert packer.post("/shipments/999999/ship",
                       headers={"If-Match": '"1"'}).status_code == 404
    cells = admin.get("/permissions").json()
    for cell in cells:
        if cell["role"] == "stockman" and cell["section"] == "shipping":
            cell["can_edit"] = False
    assert admin.put("/permissions", json=cells).status_code == 200
    problem(packer.post("/shipments/999999/ship", headers={"If-Match": '"1"'}),
            403, "forbidden")


def test_granted_permission_applies_on_next_request(admin, customer):
    problem(customer.get("/users"), 403, "forbidden")
    cells = admin.get("/permissions").json()
    for cell in cells:
        if cell["role"] == "manager" and cell["section"] == "users":
            cell["can_view"] = True
    assert admin.put("/permissions", json=cells).status_code == 200
    assert customer.get("/users").status_code == 200


def test_role_change_applies_on_next_request(admin, customer, wh):
    assert flows.create_order(customer, wh[flows.WH3])["status"] == "created"
    resp = admin.patch(f"/users/{customer.id}",
                       json={"role": "stockman", "email": customer.user["email"]})
    assert resp.status_code == 200, resp.text
    resp = customer.post("/orders",
                         json={"from_warehouse_id": wh[flows.WH3],
                               "positions": flows.positions((flows.PIPE, 1))},
                         headers={"Idempotency-Key": "after-role-change"})
    problem(resp, 403, "forbidden")


def test_view_without_edit_still_allows_reading(packer, accepted_order):
    """Кладовщик читает заказы (orders: view), но не меняет их (orders: edit)."""
    assert packer.get(f"/orders/{accepted_order['id']}").status_code == 200
    problem(packer.post(f"/orders/{accepted_order['id']}/cancel", json={},
                        headers={"If-Match": '"2"'}), 403, "forbidden")
