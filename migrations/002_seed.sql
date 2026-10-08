INSERT INTO warehouses (id, code, name, city) VALUES
 (1,'MUM','Bhiwandi Main','Mumbai'),
 (2,'BLR','Hoskote Depot','Bengaluru'),
 (3,'DEL','Narela Satellite','Delhi')
ON CONFLICT DO NOTHING;
SELECT setval('warehouses_id_seq', GREATEST((SELECT MAX(id) FROM warehouses),1));

INSERT INTO items (id, sku, name, lead_time_days, safety_stock, pack_size, min_order) VALUES
 (1,'KB-104','Mechanical Keyboard',      14, 20, 10,  0),
 (2,'MS-220','Wireless Mouse',            7, 15, 12,  0),
 (3,'HD-530','Over-ear Headphones',      21, 10,  6, 12),
 (4,'CB-001','USB-C Cable 1m',            5, 60, 50,  0),
 (5,'MN-270','27-inch Monitor',          30,  4,  2,  2),
 (6,'DK-900','Docking Station',          10,  6,  4,  0)
ON CONFLICT DO NOTHING;
SELECT setval('items_id_seq', GREATEST((SELECT MAX(id) FROM items),1));

-- Opening position. CB-001 and MS-220 in MUM are deliberately thin, so the
-- very first POST /check has something to say.
INSERT INTO stock (item_id, warehouse_id, on_hand, on_order) VALUES
 (1,1,140,0), (1,2, 60,0), (1,3, 35,0),
 (2,1, 48,0), (2,2, 95,0), (2,3, 40,0),
 (3,1, 55,0), (3,2, 18,0), (3,3, 24,0),
 (4,1, 90,0), (4,2,420,0), (4,3,150,0),
 (5,1, 12,0), (5,2,  9,0), (5,3,  5,0),
 (6,1, 30,0), (6,2, 22,0), (6,3,  8,0)
ON CONFLICT DO NOTHING;

-- Thirty days of trading history. Different items sell at very different
-- rates, which is what makes the reorder points interesting.
INSERT INTO movements (item_id, warehouse_id, delta, kind, moved_at)
SELECT r.item_id, r.warehouse_id, -r.units, 'sale',
       now() - make_interval(days => g, hours => (r.item_id * 3) % 20)
FROM generate_series(0, 29) AS g,
     (VALUES (1,1,3),(1,2,1),(1,3,1),
             (2,1,4),(2,2,2),(2,3,1),
             (3,1,1),(3,2,1),(3,3,1),
             (4,1,9),(4,2,6),(4,3,4),
             (6,1,1),(6,2,1),(6,3,1)) AS r(item_id, warehouse_id, units)
WHERE (g + r.item_id) % 2 = 0;

-- Supplier deliveries, so the history is not all one direction.
INSERT INTO movements (item_id, warehouse_id, delta, kind, moved_at)
SELECT r.item_id, r.warehouse_id, r.units, 'receipt',
       now() - make_interval(days => r.days_ago)
FROM (VALUES (1,1,100,21),(2,2,120,17),(4,2,500,12),(4,1,100,9),
             (3,1, 30,25),(6,1, 24,14)) AS r(item_id, warehouse_id, units, days_ago);

-- A couple of write-offs. Real stock loss, but NOT demand - these must not
-- inflate anybody's forecast.
INSERT INTO movements (item_id, warehouse_id, delta, kind, moved_at)
SELECT r.item_id, r.warehouse_id, -r.units, 'scrap',
       now() - make_interval(days => r.days_ago)
FROM (VALUES (5,3,1,6),(3,2,2,11)) AS r(item_id, warehouse_id, units, days_ago);

-- A historic warehouse-to-warehouse transfer, already recorded as two legs
-- sharing one id. MN-270 left Mumbai and arrived in Delhi. Nothing was sold.
-- Run GET /stock?sku=MN-270 and watch avg_daily_use stay at 0.0 for both.
INSERT INTO movements (item_id, warehouse_id, delta, kind, transfer_id, moved_at) VALUES
 (5,1,-6,'transfer_out','seed-transfer-0001', now() - make_interval(days => 4)),
 (5,3, 6,'transfer_in', 'seed-transfer-0001', now() - make_interval(days => 4));
