CREATE TABLE IF NOT EXISTS warehouses (
    id SERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    city TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS items (
    id SERIAL PRIMARY KEY,
    sku TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    lead_time_days INT NOT NULL DEFAULT 7 CHECK (lead_time_days >= 0),
    safety_stock INT NOT NULL DEFAULT 0 CHECK (safety_stock >= 0),
    pack_size INT NOT NULL DEFAULT 1 CHECK (pack_size >= 1),
    min_order INT NOT NULL DEFAULT 0 CHECK (min_order >= 0));

CREATE TABLE IF NOT EXISTS stock (
    item_id INT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    warehouse_id INT NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    on_hand INT NOT NULL DEFAULT 0,
    on_order INT NOT NULL DEFAULT 0 CHECK (on_order >= 0),
    PRIMARY KEY (item_id, warehouse_id));

CREATE TABLE IF NOT EXISTS movements (
    id SERIAL PRIMARY KEY,
    item_id INT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    warehouse_id INT NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    delta INT NOT NULL,
    kind TEXT NOT NULL,
    transfer_id TEXT,
    moved_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS movements_lookup
    ON movements (item_id, warehouse_id, moved_at DESC);
CREATE INDEX IF NOT EXISTS movements_transfer ON movements (transfer_id);

CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    item_id INT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    warehouse_id INT NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    on_hand INT NOT NULL,
    reorder_point NUMERIC(10,2) NOT NULL,
    suggested_qty INT NOT NULL,
    raised_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS alerts_recent ON alerts (raised_at DESC);
