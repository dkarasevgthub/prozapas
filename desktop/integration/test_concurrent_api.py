"""Real concurrent HTTP requests, separate sessions and committed transactions."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import unittest
import uuid

from app.api.errors import ApiError
from app.api.resources import Resources
from app.api.transport import Transport


class ConcurrentAPITests(unittest.TestCase):
    def actor(self, login):
        resource = Resources(Transport(os.environ["PROZAPAS_API"]))
        resource.login(login, "system-test-password")
        self.addCleanup(resource.logout)
        return resource

    def setUp(self):
        self.manager = self.actor("o.egorova")
        self.sender = self.actor("e.morozova")
        self.packer = self.actor("p.nikitin")
        self.article = "RACE-" + uuid.uuid4().hex[:8]
        self.item = self.sender.create_item(self.article, "Concurrent stock", "шт.", unit_weight=1)
        self.warehouses = {w["code"]: w["id"] for w in self.manager.bootstrap()["warehouses"]}
        self.packer.stock_operation(self.article, self.warehouses["129"], "receipt", 100)

    def race(self, actor, operations):
        barrier = threading.Barrier(len(operations))

        def execute(operation):
            transport = Transport(os.environ["PROZAPAS_API"])
            transport.set_tokens(actor.t._access, actor.t.refresh_token)
            resource = Resources(transport)
            barrier.wait(timeout=10)
            try:
                return 200, operation(resource)
            except ApiError as error:
                return error.status, error.title

        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(execute, operations))

    def order(self, quantity):
        return self.manager.create_order(self.warehouses["129"], [{"article": self.article, "qty": quantity}])

    def test_simultaneous_idempotent_creation_makes_one_order(self):
        key = uuid.uuid4().hex
        body = {"from_warehouse_id": self.warehouses["129"],
                "positions": [{"article": self.article, "qty": 1}]}
        result = self.race(self.manager, [lambda client: client.t.post("/orders", body, idempotency_key=key)] * 6)
        self.assertEqual([status for status, _ in result], [200] * 6, result)
        self.assertEqual(len({order["id"] for _, order in result}), 1)

    def test_simultaneous_acceptance_reserves_stock_once(self):
        order = self.order(60)
        result = self.race(self.sender, [lambda client: client.accept_order(order["id"], order["version"])] * 4)
        self.assertEqual(sum(status == 200 for status, _ in result), 1, result)
        self.assertTrue(all(status in (200, 409) for status, _ in result), result)
        stock = self.packer.stock(q=self.article)["items"][0]
        self.assertEqual((stock["qty"], stock["reserved"], stock["free"]), (100, 60, 40))

    def test_competing_orders_cannot_overreserve_same_stock(self):
        orders = [self.order(80) for _ in range(3)]
        operations = [lambda client, order=order: client.accept_order(order["id"], order["version"])
                      for order in orders]
        result = self.race(self.sender, operations)
        self.assertEqual(sum(status == 200 for status, _ in result), 1, result)
        self.assertTrue(all(status in (200, 409) for status, _ in result), result)
        stock = self.packer.stock(q=self.article)["items"][0]
        self.assertEqual((stock["qty"], stock["reserved"], stock["free"]), (100, 80, 20))

    def test_simultaneous_pack_cannot_exceed_ordered_quantity(self):
        order = self.order(10)
        self.sender.accept_order(order["id"], order["version"])
        result = self.race(self.packer, [lambda client: client.pack_box(order["id"], self.article, 8, 8)] * 3)
        self.assertEqual(sum(status == 200 for status, _ in result), 1, result)
        self.assertTrue(all(status in (200, 422) for status, _ in result), result)
        boxes = self.packer.shipment(order["id"])["boxes"]
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0]["qty"], 8)
