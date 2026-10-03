"""Вход и токены — api.md §4."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from tests import flows
from tests.flows import WH1, problem


def _login(anon, login_, password=flows.SEED_PASSWORD):
    return anon.post("/auth/login", json={"login": login_, "password": password})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _token(user_id, warehouse_id, *, secret=flows.JWT_SECRET,
           ttl=timedelta(minutes=15), issued=None) -> str:
    issued = issued or datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "wh": warehouse_id, "iat": issued,
                       "exp": issued + ttl}, secret, algorithm="HS256")


def test_login_returns_session(anon, wh):
    resp = _login(anon, "p.sokolov")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"access", "refresh", "user", "warehouse", "permissions"}
    assert isinstance(body["access"], str) and body["access"].count(".") == 2
    assert isinstance(body["refresh"], str) and len(body["refresh"]) >= 32
    user = body["user"]
    assert user["login"] == "p.sokolov"
    assert user["full_name"] == "Соколов Пётр Николаевич"
    assert user["email"] == "p.sokolov@stalker.ru"
    assert user["role"] == "stockman"
    assert user["status"] == "active"
    assert user["position"] == "Кладовщик"
    assert user["phone"] == "+7 903 551-22-14"
    assert user["hire_date"] == "2020-06-05"
    assert user["warehouse"]["id"] == wh[WH1]
    assert body["warehouse"]["id"] == wh[WH1]
    assert body["warehouse"]["responsible"] == {"id": user["id"], "name": "Соколов П.Н."}
    cells = {p["section"]: (p["can_view"], p["can_edit"]) for p in body["permissions"]}
    assert cells == flows.MATRIX["stockman"]
    assert all(p["role"] == "stockman" for p in body["permissions"])


def test_access_token_carries_only_subject_and_warehouse(anon, wh):
    body = _login(anon, "p.sokolov").json()
    claims = jwt.decode(body["access"], flows.JWT_SECRET, algorithms=["HS256"])
    assert set(claims) == {"sub", "wh", "iat", "exp"}
    assert claims["sub"] == str(body["user"]["id"])
    assert claims["wh"] == wh[WH1]
    assert claims["exp"] - claims["iat"] == 15 * 60


def test_login_ignores_case_of_login(anon):
    resp = _login(anon, "P.SOKOLOV")
    assert resp.status_code == 200
    assert resp.json()["user"]["login"] == "p.sokolov"


def test_login_rejects_wrong_password(anon):
    body = problem(_login(anon, "p.sokolov", "wrong"), 401, "invalid-credentials")
    assert body["title"] == "Неверный логин или пароль"


def test_login_does_not_reveal_whether_login_exists(anon):
    wrong_password = _login(anon, "p.sokolov", "wrong").json()
    unknown_login = _login(anon, "nobody", "wrong").json()
    assert wrong_password == unknown_login


def test_login_by_email_is_not_accepted(anon):
    problem(_login(anon, "p.sokolov@stalker.ru"), 401, "invalid-credentials")


def test_login_rejects_blocked_user(anon):
    problem(_login(anon, "s.lebedev"), 403, "account-blocked")


def test_login_rejects_deleted_user(admin, receiver, anon):
    assert admin.delete(f"/users/{receiver.id}").status_code == 204
    problem(_login(anon, "p.sokolov"), 401, "invalid-credentials")


def test_login_updates_last_login_at(anon, sql):
    before = sql("SELECT last_login_at FROM user_account WHERE login = 'p.sokolov'")[0][0]
    assert _login(anon, "p.sokolov").status_code == 200
    after = sql("SELECT last_login_at FROM user_account WHERE login = 'p.sokolov'")[0][0]
    assert after > before
    assert datetime.now(timezone.utc) - after < timedelta(minutes=1)


def test_password_hash_is_argon2id(sql):
    row = sql("SELECT password_hash FROM user_account WHERE login = 'p.sokolov'")[0]
    assert row[0].startswith("$argon2id$")


def test_me_repeats_login_without_tokens(actor):
    morozova = actor("e.morozova")
    resp = morozova.get("/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["access"] is None and body["refresh"] is None
    assert body["user"] == morozova.user
    assert body["warehouse"] == morozova.payload["warehouse"]
    assert body["permissions"] == morozova.payload["permissions"]


@pytest.mark.parametrize("header", [None, "Basic abc", "Bearer", "Bearer not-a-jwt", "bearer "])
def test_me_rejects_missing_or_garbage_token(anon, header):
    headers = {} if header is None else {"Authorization": header}
    problem(anon.get("/auth/me", headers=headers), 401, "unauthorized")


def test_expired_access_token_is_session_expired(anon, actor):
    sokolov = actor("p.sokolov")
    stale = _token(sokolov.id, sokolov.warehouse_id,
                   issued=datetime.now(timezone.utc) - timedelta(hours=1))
    problem(anon.get("/auth/me", headers=_bearer(stale)), 401, "session-expired")


def test_access_token_signed_with_other_secret_is_rejected(anon, actor):
    sokolov = actor("p.sokolov")
    forged = _token(sokolov.id, sokolov.warehouse_id, secret="x" * 40)
    problem(anon.get("/auth/me", headers=_bearer(forged)), 401, "unauthorized")


def test_access_token_for_unknown_user_is_rejected(anon):
    problem(anon.get("/auth/me", headers=_bearer(_token(999999, 1))), 401, "unauthorized")


def test_deleted_user_loses_access_immediately(admin, receiver):
    assert admin.delete(f"/users/{receiver.id}").status_code == 204
    problem(receiver.get("/auth/me"), 401, "unauthorized")


def test_blocked_user_loses_access_and_refresh_immediately(admin, receiver, anon):
    assert admin.post(f"/users/{receiver.id}/block").status_code == 204
    problem(receiver.get("/stock/summary"), 403, "account-blocked")
    problem(anon.post("/auth/refresh", json={"refresh": receiver.refresh}),
            401, "session-expired")


def test_blocked_user_cannot_refresh_even_with_live_token(sql, receiver, anon):
    sql("UPDATE user_account SET status = 'blocked' WHERE id = :id", id=receiver.id)
    problem(anon.post("/auth/refresh", json={"refresh": receiver.refresh}),
            403, "account-blocked")


def test_refresh_rotates_the_pair(anon, receiver):
    resp = anon.post("/auth/refresh", json={"refresh": receiver.refresh})
    assert resp.status_code == 200, resp.text
    pair = resp.json()
    assert set(pair) == {"access", "refresh"}
    assert pair["refresh"] != receiver.refresh
    me = anon.get("/auth/me", headers=_bearer(pair["access"]))
    assert me.status_code == 200 and me.json()["user"]["id"] == receiver.id


def test_reused_refresh_kills_the_whole_chain(anon, receiver):
    rotated = anon.post("/auth/refresh", json={"refresh": receiver.refresh}).json()
    problem(anon.post("/auth/refresh", json={"refresh": receiver.refresh}),
            401, "session-expired")
    problem(anon.post("/auth/refresh", json={"refresh": rotated["refresh"]}),
            401, "session-expired")


def test_unknown_refresh_is_session_expired(anon):
    problem(anon.post("/auth/refresh", json={"refresh": "no-such-token"}),
            401, "session-expired")


def test_expired_refresh_is_session_expired(anon, receiver, sql):
    sql("UPDATE refresh_token SET expires_at = now() - interval '1 day' "
        "WHERE user_id = :id", id=receiver.id)
    problem(anon.post("/auth/refresh", json={"refresh": receiver.refresh}),
            401, "session-expired")


def test_refresh_lives_thirty_days(receiver, sql):
    expires = sql("SELECT expires_at FROM refresh_token WHERE user_id = :id "
                  "ORDER BY id DESC LIMIT 1", id=receiver.id)[0][0]
    left = expires - datetime.now(timezone.utc)
    assert timedelta(days=29, hours=23) < left <= timedelta(days=30)


def test_refresh_is_stored_hashed(receiver, sql):
    rows = sql("SELECT token_hash FROM refresh_token WHERE user_id = :id", id=receiver.id)
    assert rows and all(row[0] != receiver.refresh for row in rows)


def test_logout_needs_no_access_token(anon, receiver):
    resp = anon.post("/auth/logout", json={"refresh": receiver.refresh})
    assert resp.status_code == 204
    problem(anon.post("/auth/refresh", json={"refresh": receiver.refresh}),
            401, "session-expired")


def test_logout_revokes_rotated_tokens_too(anon, receiver):
    rotated = anon.post("/auth/refresh", json={"refresh": receiver.refresh}).json()
    assert anon.post("/auth/logout", json={"refresh": receiver.refresh}).status_code == 204
    problem(anon.post("/auth/refresh", json={"refresh": rotated["refresh"]}),
            401, "session-expired")


def test_logout_with_unknown_token_is_204(anon):
    assert anon.post("/auth/logout", json={"refresh": "no-such-token"}).status_code == 204


def test_access_token_survives_logout_until_it_expires(anon, receiver):
    """Access на сервере не хранится (api.md §4): после выхода он живёт до истечения."""
    assert anon.post("/auth/logout", json={"refresh": receiver.refresh}).status_code == 204
    assert receiver.get("/auth/me").status_code == 200
