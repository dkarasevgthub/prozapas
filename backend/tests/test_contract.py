"""Сверка живой OpenAPI с контрактом docs/openapi.json (api.md §11)."""
from __future__ import annotations

import json
from pathlib import Path

from tests import flows

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
METHODS = ("get", "post", "put", "patch", "delete")


def _operations(spec: dict) -> dict[tuple[str, str], dict]:
    return {(method.upper(), path): op
            for path, item in spec["paths"].items()
            for method, op in item.items() if method in METHODS}


def _live(client) -> dict:
    resp = client.get(flows.API + "/openapi.json")
    assert resp.status_code == 200
    return resp.json()


def _doc() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_operations_match_contract(client):
    live, doc = _live(client), _doc()
    assert set(_operations(live)) == set(_operations(doc))
    assert live["servers"] == doc["servers"] == [{"url": flows.API}]


def test_success_codes_match_contract(client):
    live, doc = _operations(_live(client)), _operations(_doc())
    mismatches = []
    for key, op in doc.items():
        expected = {code for code in op["responses"] if code.startswith("2")}
        actual = {code for code in live[key]["responses"] if code.startswith("2")}
        if expected != actual:
            mismatches.append(f"{key[0]} {key[1]}: контракт {sorted(expected)}, "
                              f"код {sorted(actual)}")
    assert not mismatches, "\n".join(mismatches)


def test_component_schemas_match_contract(client):
    live = _live(client)["components"]["schemas"]
    doc = _doc()["components"]["schemas"]
    mismatches = []
    for name, schema in doc.items():
        actual = live.get(name)
        if actual is None:
            mismatches.append(f"{name}: нет в живой схеме")
            continue
        expected_props = set(schema.get("properties", {}))
        actual_props = set(actual.get("properties", {}))
        if expected_props != actual_props:
            mismatches.append(
                f"{name}: нет в коде {sorted(expected_props - actual_props)}, "
                f"лишние в коде {sorted(actual_props - expected_props)}")
        if schema.get("required") is not None:
            expected_required = set(schema["required"])
            actual_required = set(actual.get("required") or [])
            if expected_required != actual_required:
                mismatches.append(
                    f"{name}: обязательные по контракту {sorted(expected_required)}, "
                    f"в коде {sorted(actual_required)}")
    assert not mismatches, "\n".join(mismatches)
