from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import cache, db
from .reorder import (
    ALL_KINDS,
    REPLENISHMENT_KINDS,
    StockError,
    classify_movement,
    evaluate,
)

app = FastAPI(title="inventory-alerts")

WINDOW_DAYS = 30
REVIEW_DAYS = 7
ALERT_TTL = 24 * 60 * 60     # one alert per item per warehouse per day


@app.get("/health")
def health():
    out = {"status": "ok", "postgres": False, "redis": False}
    try:
        db.query("SELECT 1")
        out["postgres"] = True
    except Exception as e:
        out["pg_error"] = str(e)
    try:
        cache.client().ping()
        out["redis"] = True
    except Exception as e:
        out["redis_error"] = str(e)
    return out if out["postgres"] and out["redis"] else JSONResponse(out, status_code=503)


def _dedup_key(item_id, wh_id):
    return f"alert:{item_id}:{wh_id}"


def _warehouse(code):
    row = db.one("SELECT id, code, name FROM warehouses WHERE code=%s", (str(code).upper(),))
    if not row:
        raise HTTPException(404, f"no warehouse with code {code!r}")
    return row


def _item(sku):
    row = db.one("SELECT id, sku, name, lead_time_days, safety_stock, pack_size,"
                 " min_order FROM items WHERE sku=%s", (str(sku).upper(),))
    if not row:
        raise HTTPException(404, f"no item with sku {sku!r}")
    return row


def _movements(item_id, wh_id):
    return db.query(
        "SELECT kind, delta, transfer_id, moved_at FROM movements"
        " WHERE item_id=%s AND warehouse_id=%s"
        " AND moved_at > now() - make_interval(days => %s)",
        (item_id, wh_id, WINDOW_DAYS))


@app.get("/warehouses")
def warehouses():
    return {"warehouses": db.query(
        "SELECT w.id, w.code, w.name, w.city,"
        " coalesce(sum(s.on_hand), 0) AS units,"
        " count(s.item_id) AS skus"
        " FROM warehouses w LEFT JOIN stock s ON s.warehouse_id = w.id"
        " GROUP BY w.id ORDER BY w.code")}


@app.get("/stock")
def stock(warehouse: str = "", sku: str = ""):
    """Live position, with the reorder point worked out for each line."""
    sql = ("SELECT i.id AS item_id, i.sku, i.name, i.lead_time_days, i.safety_stock,"
           " i.pack_size, i.min_order, w.id AS wh_id, w.code AS warehouse,"
           " s.on_hand, s.on_order"
           " FROM stock s JOIN items i ON i.id=s.item_id"
           " JOIN warehouses w ON w.id=s.warehouse_id WHERE TRUE")
    params = []
    if warehouse:
        sql += " AND w.code=%s"
        params.append(warehouse.upper())
    if sku:
        sql += " AND i.sku=%s"
        params.append(sku.upper())
    sql += " ORDER BY w.code, i.sku"
    rows = db.query(sql, tuple(params))
    if not rows:
        raise HTTPException(404, "no stock matches that filter")

    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        verdict = evaluate(
            {"sku": r["sku"], "on_hand": r["on_hand"], "on_order": r["on_order"],
             "lead_time_days": r["lead_time_days"], "safety_stock": r["safety_stock"],
             "pack_size": r["pack_size"], "min_order": r["min_order"]},
            _movements(r["item_id"], r["wh_id"]), now, WINDOW_DAYS, REVIEW_DAYS)
        out.append({"warehouse": r["warehouse"], "name": r["name"], **verdict})
    return {"as_of": now.isoformat(), "window_days": WINDOW_DAYS, "lines": out}


@app.post("/movements", status_code=201)
def movement(payload: dict = Body(...)):
    """Record a sale, a receipt or a write-off against one warehouse."""
    item = _item(payload.get("sku", ""))
    wh = _warehouse(payload.get("warehouse", ""))
    kind = str(payload.get("kind", "")).strip().lower()
    if kind not in ALL_KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(ALL_KINDS)}")
    if kind in ("transfer_out", "transfer_in"):
        raise HTTPException(400, "use POST /transfers to move stock between warehouses")
    try:
        qty = int(payload.get("qty", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "qty must be a whole number")
    if qty <= 0:
        raise HTTPException(400, "qty must be positive")

    delta = qty if kind in REPLENISHMENT_KINDS else -qty
    try:
        meaning = classify_movement(kind, delta, None)
    except StockError as e:
        raise HTTPException(400, str(e))

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT on_hand FROM stock WHERE item_id=%s AND warehouse_id=%s"
                    " FOR UPDATE", (item["id"], wh["id"]))
        row = cur.fetchone()
        have = row["on_hand"] if row else 0
        if have + delta < 0:
            raise HTTPException(400, f"only {have} units of {item['sku']} in {wh['code']}")
        cur.execute("INSERT INTO stock (item_id, warehouse_id, on_hand) VALUES (%s,%s,%s)"
                    " ON CONFLICT (item_id, warehouse_id)"
                    " DO UPDATE SET on_hand = stock.on_hand + EXCLUDED.on_hand",
                    (item["id"], wh["id"], delta))
        cur.execute("INSERT INTO movements (item_id, warehouse_id, delta, kind)"
                    " VALUES (%s,%s,%s,%s) RETURNING id, moved_at",
                    (item["id"], wh["id"], delta, kind))
        rec = cur.fetchone()
    return {"movement_id": rec["id"], "sku": item["sku"], "warehouse": wh["code"],
            "kind": kind, "delta": delta, "counts_as": meaning,
            "on_hand": have + delta, "moved_at": rec["moved_at"].isoformat()}


@app.post("/transfers", status_code=201)
def transfer(payload: dict = Body(...)):
    """Move stock between two of your own warehouses.

    Writes TWO movement rows sharing one transfer_id, inside one transaction.
    Neither leg is demand - see reorder.classify_movement.
    """
    item = _item(payload.get("sku", ""))
    src = _warehouse(payload.get("from", ""))
    dst = _warehouse(payload.get("to", ""))
    if src["id"] == dst["id"]:
        raise HTTPException(409, "source and destination warehouse are the same")
    try:
        qty = int(payload.get("qty", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "qty must be a whole number")
    if qty <= 0:
        raise HTTPException(400, "qty must be positive")

    tid = str(uuid4())
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT on_hand FROM stock WHERE item_id=%s AND warehouse_id=%s"
                    " FOR UPDATE", (item["id"], src["id"]))
        row = cur.fetchone()
        have = row["on_hand"] if row else 0
        if have < qty:
            raise HTTPException(400, f"only {have} units of {item['sku']} in {src['code']}")
        cur.execute("UPDATE stock SET on_hand = on_hand - %s"
                    " WHERE item_id=%s AND warehouse_id=%s", (qty, item["id"], src["id"]))
        cur.execute("INSERT INTO stock (item_id, warehouse_id, on_hand) VALUES (%s,%s,%s)"
                    " ON CONFLICT (item_id, warehouse_id)"
                    " DO UPDATE SET on_hand = stock.on_hand + EXCLUDED.on_hand",
                    (item["id"], dst["id"], qty))
        cur.execute("INSERT INTO movements (item_id, warehouse_id, delta, kind, transfer_id)"
                    " VALUES (%s,%s,%s,'transfer_out',%s)",
                    (item["id"], src["id"], -qty, tid))
        cur.execute("INSERT INTO movements (item_id, warehouse_id, delta, kind, transfer_id)"
                    " VALUES (%s,%s,%s,'transfer_in',%s)",
                    (item["id"], dst["id"], qty, tid))
    return {"transfer_id": tid, "sku": item["sku"], "qty": qty,
            "from": src["code"], "to": dst["code"],
            "counts_as": classify_movement("transfer_out", -qty, tid),
            "note": "neither leg counts as consumption"}


@app.post("/check")
def check():
    """Run the low-stock checker over every stocked line.

    Redis earns its place here. Without it this endpoint raises the same alert
    for the same sorry item every single time it runs - every five minutes,
    forever. The de-duplication key is set with NX so only the first checker to
    see the problem wins, and it is DELETED as soon as the line recovers, so a
    genuine second dip alerts again.
    """
    rows = db.query(
        "SELECT i.id AS item_id, i.sku, i.name, i.lead_time_days, i.safety_stock,"
        " i.pack_size, i.min_order, w.id AS wh_id, w.code AS warehouse,"
        " s.on_hand, s.on_order"
        " FROM stock s JOIN items i ON i.id=s.item_id"
        " JOIN warehouses w ON w.id=s.warehouse_id ORDER BY w.code, i.sku")

    now = datetime.now(timezone.utc)
    raised, suppressed, healthy = [], [], 0
    c = cache.client()
    for r in rows:
        verdict = evaluate(
            {"sku": r["sku"], "on_hand": r["on_hand"], "on_order": r["on_order"],
             "lead_time_days": r["lead_time_days"], "safety_stock": r["safety_stock"],
             "pack_size": r["pack_size"], "min_order": r["min_order"]},
            _movements(r["item_id"], r["wh_id"]), now, WINDOW_DAYS, REVIEW_DAYS)
        key = _dedup_key(r["item_id"], r["wh_id"])
        if not verdict["below_reorder_point"]:
            healthy += 1
            c.delete(key)        # recovered: let the next dip speak up
            continue
        line = {"warehouse": r["warehouse"], **verdict}
        if c.set(key, now.isoformat(), nx=True, ex=ALERT_TTL):
            db.query("INSERT INTO alerts (item_id, warehouse_id, on_hand,"
                     " reorder_point, suggested_qty) VALUES (%s,%s,%s,%s,%s)",
                     (r["item_id"], r["wh_id"], verdict["on_hand"],
                      verdict["reorder_point"], verdict["suggested_qty"]), fetch=False)
            raised.append(line)
        else:
            suppressed.append({"warehouse": r["warehouse"], "sku": r["sku"],
                               "already_alerted": True,
                               "seconds_until_repeat": c.ttl(key)})
    return {"checked": len(rows), "healthy": healthy,
            "raised": raised, "suppressed": suppressed}


@app.get("/alerts")
def alerts(limit: int = 20):
    if limit < 1 or limit > 200:
        raise HTTPException(400, "limit must be between 1 and 200")
    return {"alerts": db.query(
        "SELECT a.id, i.sku, i.name, w.code AS warehouse, a.on_hand,"
        " a.reorder_point, a.suggested_qty, a.raised_at"
        " FROM alerts a JOIN items i ON i.id=a.item_id"
        " JOIN warehouses w ON w.id=a.warehouse_id"
        " ORDER BY a.raised_at DESC, a.id DESC LIMIT %s", (limit,))}
