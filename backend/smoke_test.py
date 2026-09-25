"""Сквозной прогон ProЗапас: создание заказа -> accept -> коробка -> ship
-> receive -> complete, плюс проверки прав, ETag и идемпотентности.

Запуск: сервер поднят (uvicorn api.main:app --reload), затем:
    python smoke_test.py
Только стандартная библиотека.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000/api/v1"
ADMIN_LOGIN = "admin"                                    # логин админа из seed.py
ADMIN_PASSWORD = os.environ.get("SEED_PASSWORD", "1234")     # или впишите пароль сюда

uniq = str(int(time.time()))          # суффикс уникальности: можно запускать повторно


def req(method, path, token=None, body=None, headers=None):
    r = urllib.request.Request(BASE + path, method=method)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, data=data) as resp:
            raw = resp.read()
            return (resp.status, json.loads(raw) if raw else None,
                    {k.lower(): v for k, v in resp.headers.items()})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"raw": raw.decode(errors="replace")}
        return e.code, payload, {k.lower(): v for k, v in e.headers.items()}


def check(name, cond, detail=""):
    print(("[OK ] " if cond else "[ПРОВАЛ] ") + name)
    if not cond:
        print("      " + str(detail))
        sys.exit(1)


def login(login_, password):
    s, d, _ = req("POST", "/auth/login",
                  body={"login": login_, "password": password})
    check(f"вход {login_}", s == 200, d)
    return d["access"]


# --- Подготовка: админ, справочники, три сотрудника -----------------------------
admin = login(ADMIN_LOGIN, ADMIN_PASSWORD)

s, boot, _ = req("GET", "/bootstrap", admin)
check("bootstrap", s == 200 and len(boot["warehouses"]) >= 2,
      f"складов: {len(boot.get('warehouses', []))} — нужно минимум 2")
w1, w2 = boot["warehouses"][0]["id"], boot["warehouses"][1]["id"]
roles = {r["code"] for r in boot["roles"]}

s, item, _ = req("POST", "/catalog", admin, body={
    "article": f"SMK-{uniq}", "name": f"Дым-тест {uniq}",
    "unit": "шт", "unit_weight": 1.6})
check("создана позиция справочника", s == 201, item)
article = item["article"]

people = {}
for name, role, wh in (("creator", "manager", w1),
                       ("sender", "admin", w2),
                       ("receiver", "stockman", w1)):
    login_ = f"smoke_{name}_{uniq}"
    s, u, _ = req("POST", "/users", admin, body={
        "full_name": f"Дымовой {name}", "login": login_,
        "email": f"{login_}@example.com", "password": "secret123",
        "role": role, "warehouse_id": wh})
    check(f"создан {name} ({role} на складе {wh})", s == 201, u)
    people[name] = {"token": login(login_, "secret123"), "id": u["id"]}

# --- Права: менеджеру нельзя в пользователей ------------------------------------
s, d, _ = req("GET", "/users", people["creator"]["token"])
check("manager на /users -> 403", s == 403, d)

# --- Создание заказа + идемпотентность ------------------------------------------
idem = str(uuid.uuid4())
order_body = {"from_warehouse_id": w2, "comment": "дымовой прогон",
              "positions": [{"article": article, "qty": 100}]}
s, order, _ = req("POST", "/orders", people["creator"]["token"],
                  order_body, {"Idempotency-Key": idem})
check("заказ создан (201)", s == 201, order)
oid = order["id"]

s, again, _ = req("POST", "/orders", people["creator"]["token"],
                  order_body, {"Idempotency-Key": idem})
check("повтор с тем же ключом -> тот же заказ",
      s == 201 and again["id"] == oid, again)

# --- Accept: ETag, повторный accept -> 409 --------------------------------------
s, card, h = req("GET", f"/orders/{oid}", people["sender"]["token"])
check("карточка заказа у отправителя", s == 200 and "etag" in h, card)

s, d, _ = req("POST", f"/orders/{oid}/accept", people["sender"]["token"],
              headers={"If-Match": h["etag"]})
check("accept -> processing", s == 200 and d["status"] == "processing", d)

s, d, _ = req("POST", f"/orders/{oid}/accept", people["sender"]["token"],
              headers={"If-Match": h["etag"]})
check("повторный accept -> 409", s == 409, d)

s, d, _ = req("POST", f"/orders/{oid}/accept", people["sender"]["token"])
check("accept без If-Match -> 428", s == 428, d)

# --- Упаковка: штрихкод от сервера, перебор -> 422 ------------------------------
s, ship_card, h = req("GET", f"/shipments/{oid}", people["sender"]["token"])
check("карточка отгрузки", s == 200 and h.get("etag"), ship_card)

s, box, _ = req("POST", f"/shipments/{oid}/boxes", people["sender"]["token"],
                {"article": article, "qty": 100, "weight": 160.0})
check("коробка создана, штрихкод от сервера",
      s == 201 and box.get("barcode", "").startswith("WH"), box)

s, d, _ = req("POST", f"/shipments/{oid}/boxes", people["sender"]["token"],
              {"article": article, "qty": 5, "weight": 8.0})
check("больше заказанного -> 422", s == 422, d)

# --- Ship: частичной недостачи нет, повтор -> 409 --------------------------------
s, res, _ = req("POST", f"/shipments/{oid}/ship", people["sender"]["token"],
                headers={"If-Match": h["etag"]})
check("ship проведён, недостачи нет",
      s == 200 and res.get("shortage") == [], res)

s, d, _ = req("POST", f"/shipments/{oid}/ship", people["sender"]["token"],
              headers={"If-Match": h["etag"]})
check("повторный ship -> 409", s == 409, d)

# --- Приёмка: скан, расхождение, идемпотентность, complete ----------------------
s, rec, h = req("GET", f"/receipts/{oid}", people["receiver"]["token"])
check("карточка приёмки у получателя", s == 200 and h.get("etag"), rec)
barcode = rec["boxes"][0]["barcode"]

s, rb, _ = req("POST", f"/receipts/{oid}/boxes/{barcode}/receive",
               people["receiver"]["token"], {"actual_weight": 158.0})
check("скан коробки, расхождение -2 кг",
      s == 200 and abs(rb["diff_kg"] + 2.0) < 1e-9, rb)

s, rb2, _ = req("POST", f"/receipts/{oid}/boxes/{barcode}/receive",
                people["receiver"]["token"], {"actual_weight": 158.0})
check("повторный скан -> 200, та же коробка",
      s == 200 and rb2["received_at"] is not None, rb2)

s, done, _ = req("POST", f"/receipts/{oid}/complete",
                 people["receiver"]["token"],
                 headers={"If-Match": h["etag"]})
check("приёмка завершена, недостачи нет",
      s == 200 and done.get("missing") == [], done)

# --- Итоговое состояние ----------------------------------------------------------
s, card, _ = req("GET", f"/orders/{oid}", people["creator"]["token"])
check("заказ в статусе received", card["status"] == "received", card)

s, stock, _ = req("GET", f"/stock?in_stock=false&q={article}",
                  people["sender"]["token"])
row = next((r for r in stock["items"] if r["article"] == article), None)
check("у отправителя остаток списан до 0",
      row is not None and row["qty"] == 0, stock)

s, stock, _ = req("GET", f"/stock?q={article}",
                  people["receiver"]["token"])
row = next((r for r in stock["items"] if r["article"] == article), None)
check("у получателя оприходовано 100",
      row is not None and row["qty"] == 100, stock)

s, act, _ = req("GET", f"/users/{people['sender']['id']}/activity", admin)
check("audit_log пишется (activity непуста)",
      s == 200 and act["total"] > 0, act)

print("\nИТОГ: сквозной сценарий пройден целиком.")