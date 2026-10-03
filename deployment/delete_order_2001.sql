-- One-off cleanup of the completed deployment smoke-test order.
-- Run with psql -v ON_ERROR_STOP=1 after making a database backup.
BEGIN;
LOCK TABLE public."order", stock_balance, stock_movement IN SHARE ROW EXCLUSIVE MODE;

CREATE TEMP TABLE target_order ON COMMIT DROP AS
SELECT id, number, status, comment FROM public."order" WHERE number = '2001';

DO $$
BEGIN
    IF (SELECT count(*) FROM target_order) <> 1 THEN
        RAISE EXCEPTION 'Expected exactly one order 2001';
    END IF;
    IF EXISTS (SELECT 1 FROM target_order WHERE status <> 'received'
               OR comment IS NULL OR comment NOT LIKE 'Проверка сетевого развёртывания:%') THEN
        RAISE EXCEPTION 'Order 2001 is not the completed deployment smoke test';
    END IF;
END $$;

CREATE TEMP TABLE inventory_undo ON COMMIT DROP AS
SELECT m.item_id, m.warehouse_id, sum(m.delta) AS delta
FROM stock_movement m JOIN target_order o ON m.doc_type = 'order' AND m.doc_id = o.id
WHERE m.type IN ('shipment', 'receipt')
GROUP BY m.item_id, m.warehouse_id;

DO $$
BEGIN
    IF (SELECT count(*) FROM inventory_undo) <> 2
       OR (SELECT sum(delta) FROM inventory_undo) <> 0 THEN
        RAISE EXCEPTION 'Unexpected inventory effects for order 2001';
    END IF;
    IF EXISTS (
        SELECT 1 FROM inventory_undo u LEFT JOIN stock_balance b
          ON b.item_id = u.item_id AND b.warehouse_id = u.warehouse_id
        WHERE b.item_id IS NULL OR b.qty - u.delta < b.reserved
    ) THEN
        RAISE EXCEPTION 'Cannot safely undo the inventory transfer';
    END IF;
    IF EXISTS (
        SELECT 1 FROM stock_movement m JOIN inventory_undo u
          ON u.item_id = m.item_id AND u.warehouse_id = m.warehouse_id
        WHERE NOT (m.doc_type = 'order' AND m.doc_id = (SELECT id FROM target_order))
           OR m.doc_type IS NULL OR m.doc_id IS NULL
    ) THEN
        RAISE EXCEPTION 'Other inventory movements exist; automatic demo cleanup refused';
    END IF;
END $$;

UPDATE stock_balance b SET qty = b.qty - u.delta,
       version = b.version + 1
FROM inventory_undo u
WHERE b.item_id = u.item_id AND b.warehouse_id = u.warehouse_id;

DELETE FROM stock_movement m USING target_order o
WHERE m.doc_type = 'order' AND m.doc_id = o.id;

DELETE FROM idempotency_key k USING target_order o
WHERE k.response ->> 'id' = o.id::text AND k.response ->> 'number' = o.number;

INSERT INTO audit_log (entity, entity_id, action, before, after, request_id)
SELECT 'order', id, 'deleted', jsonb_build_object('number', number, 'status', status),
       jsonb_build_object('reason', 'Удаление проверочного заказа',
                          'inventory_reverted', true), 'maintenance-delete-order-2001'
FROM target_order;

-- Database foreign keys remove its positions, shipment, boxes, receipt and status events.
DELETE FROM public."order" WHERE id IN (SELECT id FROM target_order);
COMMIT;

SELECT count(*) AS remaining_order_2001 FROM public."order" WHERE number = '2001';
SELECT item_id, warehouse_id, qty, reserved FROM stock_balance WHERE item_id = 9 ORDER BY warehouse_id;
