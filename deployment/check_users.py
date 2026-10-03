"""Verify demo logins and the visible user directory over HTTPS."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "desktop"))

from app import api
from app.api.errors import Unauthorized
from app.api.transport import Transport

accounts = json.loads((ROOT / "deployment" / "demo-users.json").read_text(encoding="utf-8"))
expected = {a["login"] for a in accounts}
for account in accounts:
    transport = Transport(api.transport.base_url)
    try:
        payload = transport.login(account["login"], account["password"])
        assert payload["user"]["full_name"] == account["full_name"]
        assert payload["user"]["role"] == account["role"]
        assert payload["warehouse"]["code"] == account["warehouse"]
        directory = transport.get("/users")
        assert directory["total"] == 3
        assert {u["login"] for u in directory["items"]} == expected
        bootstrap = transport.get("/bootstrap")
        assert {u["name"] for u in bootstrap["users"]} == {a["full_name"] for a in accounts}
        print(f"{account['login']}: login, warehouse, permissions and user directory OK")
    finally:
        transport.logout()

# The previous local access file may still hold the original seed password.
previous = dict(line.split("=", 1) for line in
                (ROOT / "deployment" / "access.txt").read_text(encoding="utf-8").splitlines()
                if line.startswith(("LOGIN=", "PASSWORD=")))
if previous.get("LOGIN") == "admin":
    transport = Transport(api.transport.base_url)
    try:
        transport.login("admin", previous["PASSWORD"])
    except Unauthorized:
        print("Previous seed admin: login rejected OK")
    else:
        transport.logout()
        raise AssertionError("Old seed administrator can still log in")
