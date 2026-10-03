"""Формат ошибок — api.md §3.3: RFC 9457, коды по таблице, русский title."""
from __future__ import annotations

import pytest

from tests import flows
from tests.flows import problem


def _cyrillic(text: str) -> bool:
    return any("Ѐ" <= ch <= "ӿ" for ch in text)


def test_unknown_path_is_problem_json(anon):
    body = problem(anon.get("/no-such-path"), 404, "not-found")
    assert body["title"] == "Не найдено"


def test_method_not_allowed_is_problem_json(anon):
    body = problem(anon.request("DELETE", "/health"), 405, "method-not-allowed")
    assert body["title"] == "Метод не поддерживается этим адресом"


def test_unauthorized_is_problem_json(anon):
    body = problem(anon.get("/catalog"), 401, "unauthorized")
    assert body["title"] == "Требуется вход"


def test_domain_rule_violation_is_422(customer, wh):
    resp = customer.post("/orders",
                         json={"from_warehouse_id": wh[flows.WH3], "positions": []},
                         headers={"Idempotency-Key": "empty-positions"})
    body = problem(resp, 422, "unprocessable")
    assert _cyrillic(body["title"])


def test_problem_titles_are_russian_even_for_router_errors(anon, admin):
    for resp in (anon.get("/nope"), anon.request("PUT", "/health"),
                 admin.post("/catalog", json={"article": 1})):
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert _cyrillic(resp.json()["title"]), resp.json()


def test_precondition_required_is_428(sender, accepted_order):
    body = problem(sender.post(f"/orders/{accepted_order['id']}/accept"),
                   428, "precondition-required")
    assert body["title"] == "Нужен заголовок If-Match"


MALFORMED = [
    ("tab missing", "GET", "/orders", {}),
    ("tab unknown", "GET", "/orders", {"params": {"tab": "sideways"}}),
    ("status unknown", "GET", "/orders", {"params": {"tab": "outgoing", "status": "bogus"}}),
    ("date unparsable", "GET", "/orders",
     {"params": {"tab": "outgoing", "created_from": "yesterday"}}),
    ("limit zero", "GET", "/catalog", {"params": {"limit": 0}}),
    ("limit above max", "GET", "/catalog", {"params": {"limit": 201}}),
    ("offset negative", "GET", "/catalog", {"params": {"offset": -1}}),
    ("path id not int", "GET", "/orders/abc", {}),
    ("body not json", "POST", "/auth/login",
     {"content": b"not json", "headers": {"Content-Type": "application/json"}}),
    ("body wrong shape", "POST", "/auth/login", {"json": ["login", "password"]}),
    ("body without required field", "POST", "/catalog",
     {"json": {"article": "X-1", "unit": "шт"}}),
    ("field wrong type", "POST", "/orders",
     {"json": {"from_warehouse_id": "three",
               "positions": [{"article": "100512", "qty": 1}]},
      "headers": {"Idempotency-Key": "typed"}}),
    ("enum unknown in body", "POST", "/stock/operations",
     {"json": {"article": "100421", "warehouse_id": 1, "type": "reserve", "qty": 1}}),
    ("users status unknown", "GET", "/users", {"params": {"status": "bogus"}}),
    ("movement type unknown", "GET", "/stock/1/movements", {"params": {"type": "bogus"}}),
    ("logout without body", "POST", "/auth/logout", {}),
    ("if-match not a number", "POST", "/orders/1/accept", {"headers": {"If-Match": "abc"}}),
]


@pytest.mark.parametrize("case", MALFORMED, ids=[c[0] for c in MALFORMED])
def test_unparsable_input_is_400(admin, case):
    """api.md §3.3: 400 — тело или параметры не разобрались; 422 — доменное правило."""
    _name, method, path, kw = case
    problem(admin.request(method, path, **kw), 400, "bad-request")
