"""Verify the configured desktop HTTP client against the demo deployment.

Creates and completes one small demo transfer, leaving its history visible.
Credentials are read from the ignored access.txt and never logged.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "desktop"))

from app import api, config
from app.api.resources import Resources
from app.api.transport import Transport


def main():
    access = dict(line.split("=", 1) for line in
                  (ROOT / "deployment" / "access.txt").read_text(encoding="utf-8").splitlines()
                  if "=" in line)
    assert config.flag("PROZAPAS_TLS_VERIFY"), "TLS verification must stay enabled"
    assert api.transport.base_url == access["API"]
    assert api.client.health()["status"] == "ok"
    print("Configured desktop client: HTTPS health OK (certificate verified)")

    actors = []
    def actor(login):
        transport = Transport(access["API"], verify_tls=True)
        resources = Resources(transport)
        resources.login(login, access["PASSWORD"])
        actors.append(resources)
        return resources

    try:
        admin = actor("karasev")
        bootstrap = admin.bootstrap()
        assert len(bootstrap["warehouses"]) == 4
        assert admin.t.get("/catalog")["total"] == 22
        assert admin.dashboard() is not None
        spec = admin.t.get("/openapi.json")
        methods = {"get", "post", "put", "patch", "delete"}
        operations = sum(method in methods for path in spec["paths"].values() for method in path)
        print(f"Admin login, bootstrap, catalog, dashboard and {operations} API operations: OK")

        customer = admin
        sender = actor("litvin")
        packer = sender
        receiver = customer
        warehouses = {w["code"]: w["id"] for w in bootstrap["warehouses"]}
        article, quantity, weight = "100512", 0.5, 0.8
        row = next(r for r in packer.stock(q=article)["items"] if r["article"] == article)
        item_id = row["item_id"]

        def balances():
            rows = admin.stock_by_warehouse(item_id)
            return {r["warehouse"]["id"]: (r["qty"], r["reserved"]) for r in rows}

        before = balances()
        order = customer.create_order(
            warehouses["129"], [{"article": article, "qty": quantity}],
            "Проверка сетевого развёртывания: полный цикл по HTTPS",
        )
        order_id = order["id"]
        sender.accept_order(order_id, sender.order(order_id)["version"])
        reserved = balances()
        assert reserved[warehouses["129"]][1] == before[warehouses["129"]][1] + quantity
        box = packer.pack_box(order_id, article, quantity, weight)
        packer.ship(order_id, packer.shipment(order_id)["version"])
        receiver.receive_box(order_id, box["barcode"], weight)
        receiver.complete_receipt(order_id, receiver.receipt(order_id)["version"])
        assert customer.order(order_id)["status"] == "received"
        after = balances()
        assert after[warehouses["129"]][0] == before[warehouses["129"]][0] - quantity
        assert after[warehouses["128"]][0] == before.get(warehouses["128"], (0, 0))[0] + quantity
        assert after[warehouses["129"]][1] == before[warehouses["129"]][1]
        print(f"Demo order {order['number']}: reserve, pack, ship, receive and stock balances OK")
    finally:
        for resources in actors:
            resources.logout()


if __name__ == "__main__":
    main()
