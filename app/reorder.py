"""Pure reorder logic. No database, no HTTP, no clock of its own.

Every function here takes plain data and returns plain data. `as_of` is always
a parameter, never `datetime.now()`, so a test can ask "what did this look like
last Tuesday?" without mocking anything.

The idea
--------
An item sitting in a warehouse is consumed at some average daily rate. If you
order more only when the shelf is empty you are out of stock for the whole of
the supplier's lead time. So you reorder earlier, at the **reorder point**:

    reorder point = average daily use x lead time + safety stock

and you order enough to cover the lead time *and* the gap until the next time
anybody looks at this item (the review period).

What to test
------------
* ``classify_movement`` - every (kind, delta, transfer_id) combination. This is
  the heart of the hard part: a transfer leg is NEVER consumption, whatever its
  sign, because the goods are still yours.
* ``is_consumption`` - scrap is a loss but it is not demand, so it must not
  inflate the forecast. A receipt is positive and must not count either.
* ``consumption_in_window`` / ``average_daily_use`` - window boundaries are
  off-by-one bait. A movement exactly ``window_days`` old is OUT; one day newer
  is IN. Zero movements must give 0.0 and not divide by zero.
* ``reorder_point`` - zero lead time, zero use, fractional rates.
* ``round_to_pack`` - 1 unit with a pack size of 12 is 12, not 1. 12 is 12, not
  24. Pack size 1 and pack size 0 must both behave.
* ``suggested_order_qty`` - stock already on order must reduce the suggestion;
  a suggestion must never be negative; ``min_order`` is a floor applied before
  pack rounding, not after.
* ``days_of_cover`` - an item nobody uses has infinite cover (``None``), not a
  ZeroDivisionError.
* ``needs_reorder`` - the boundary. On hand exactly equal to the reorder point
  DOES need reordering; one unit above does not.
* ``evaluate`` - the whole decision end to end, including an item whose only
  recent movements are transfers (it must report zero use and therefore must
  not be dragged below its reorder point by the transfer out).
"""
from datetime import date, datetime, timedelta

# Movement kinds that represent real demand leaving the business.
CONSUMPTION_KINDS = frozenset({"sale", "issue"})
# Stock arriving from a supplier.
REPLENISHMENT_KINDS = frozenset({"receipt", "return"})
# Stock lost. Real, but not demand - forecasting on breakage orders rubbish.
SHRINKAGE_KINDS = frozenset({"scrap", "damage"})
# The two legs of a warehouse-to-warehouse move.
TRANSFER_KINDS = frozenset({"transfer_out", "transfer_in"})

ALL_KINDS = CONSUMPTION_KINDS | REPLENISHMENT_KINDS | SHRINKAGE_KINDS | TRANSFER_KINDS


class StockError(ValueError):
    pass


def _as_date(value):
    """Accept a date or a datetime and return a date.

    Comparing dates rather than instants keeps the window arithmetic free of
    timezones, which is one less thing for a test to get wrong.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise StockError(f"{value!r} is not a date")


def classify_movement(kind, delta, transfer_id=None):
    """What does this stock movement actually mean?

    Returns one of: "transfer", "consumption", "replenishment", "shrinkage".

    THE HARD PART LIVES HERE. Stock moving between two of your own warehouses
    looks exactly like a sale at the warehouse it leaves and exactly like a
    delivery at the warehouse it arrives in. It is neither. Nothing was sold
    and nothing was bought - the company owns the same number of units it did
    a minute ago. If you let a transfer out count as consumption, the branch
    you emptied starts forecasting demand it never had and reorders stock that
    is already sitting in the other warehouse.

    So the transfer test comes FIRST, before the sign of `delta` is even
    looked at, and it keys off `transfer_id` rather than trusting the kind
    label - a mislabelled row that carries a transfer id is still a transfer.
    """
    if transfer_id:
        return "transfer"
    k = str(kind or "").strip().lower()
    if k in TRANSFER_KINDS:
        # Carries a transfer kind but no id: still not demand.
        return "transfer"
    if k not in ALL_KINDS:
        raise StockError(f"unknown movement kind {kind!r}")
    if k in SHRINKAGE_KINDS:
        return "shrinkage"
    if k in REPLENISHMENT_KINDS:
        return "replenishment"
    return "consumption"


def is_consumption(movement):
    """Does this movement count towards demand?

    `movement` is a plain dict: {"kind": "sale", "delta": -5, "transfer_id": None}
    Only a negative, non-transfer, non-shrinkage movement counts.
    """
    delta = int(movement.get("delta", 0))
    kind = classify_movement(movement.get("kind"), delta, movement.get("transfer_id"))
    return kind == "consumption" and delta < 0


def units_consumed(movement):
    """How many units of demand this movement represents (never negative)."""
    return abs(int(movement.get("delta", 0))) if is_consumption(movement) else 0


def consumption_in_window(movements, as_of, window_days=30):
    """Total units consumed in the `window_days` days ending on `as_of`.

    The window is half open: a movement exactly `window_days` old falls out,
    one day newer falls in. Movements in the future are ignored - they are
    almost always a clock problem, not demand.
    """
    if window_days <= 0:
        raise StockError("the window must be at least one day")
    end = _as_date(as_of)
    start = end - timedelta(days=window_days)
    total = 0
    for m in movements:
        when = _as_date(m.get("moved_at"))
        if start < when <= end:
            total += units_consumed(m)
    return total


def average_daily_use(movements, as_of, window_days=30):
    """Mean units consumed per day over the window. Zero movements gives 0.0."""
    total = consumption_in_window(movements, as_of, window_days)
    return round(total / float(window_days), 4)


def reorder_point(avg_daily_use, lead_time_days, safety_stock=0):
    """The level at which an order must be placed.

    Cover the supplier's lead time, plus a buffer for the weeks when demand
    runs hot.
    """
    if lead_time_days < 0:
        raise StockError("lead time cannot be negative")
    if safety_stock < 0:
        raise StockError("safety stock cannot be negative")
    return round(float(avg_daily_use) * float(lead_time_days) + float(safety_stock), 2)


def days_of_cover(on_hand, avg_daily_use):
    """How long the shelf lasts at the current rate.

    Returns None - not an exception, and not a huge number - when nothing is
    moving. "Infinite cover" is a real answer for a dead item.
    """
    if avg_daily_use <= 0:
        return None
    return round(float(on_hand) / float(avg_daily_use), 1)


def round_to_pack(qty, pack_size=1):
    """Round UP to a whole number of supplier packs.

    You cannot buy two thirds of a carton of twelve.
    """
    q = max(0, int(qty if qty == int(qty) else int(qty) + 1))
    p = int(pack_size or 1)
    if p <= 1:
        return q
    if q == 0:
        return 0
    return ((q + p - 1) // p) * p


def needs_reorder(on_hand, rop, on_order=0):
    """At or below the reorder point, counting stock already on its way."""
    return (float(on_hand) + float(on_order)) <= float(rop)


def suggested_order_qty(on_hand, avg_daily_use, lead_time_days,
                        safety_stock=0, review_days=7, pack_size=1,
                        min_order=0, on_order=0):
    """How much to actually order.

    Target = enough to cover the lead time AND the review period (the gap
    until anybody looks at this item again), plus safety stock. Subtract what
    is on the shelf and what the supplier already owes us, apply the supplier's
    minimum, then round up to whole packs.
    """
    if review_days < 0:
        raise StockError("the review period cannot be negative")
    target = (float(avg_daily_use) * (float(lead_time_days) + float(review_days))
              + float(safety_stock))
    gap = target - float(on_hand) - float(on_order)
    if gap <= 0:
        return 0
    gap = max(gap, float(min_order))
    return round_to_pack(gap, pack_size)


def evaluate(item, movements, as_of, window_days=30, review_days=7):
    """The whole decision for one item in one warehouse.

    `item` is a plain dict:
        {"sku": "...", "on_hand": 40, "lead_time_days": 7,
         "safety_stock": 10, "pack_size": 12, "min_order": 0, "on_order": 0}
    """
    adu = average_daily_use(movements, as_of, window_days)
    rop = reorder_point(adu, item.get("lead_time_days", 0), item.get("safety_stock", 0))
    on_hand = int(item.get("on_hand", 0))
    on_order = int(item.get("on_order", 0))
    low = needs_reorder(on_hand, rop, on_order)
    qty = suggested_order_qty(
        on_hand, adu, item.get("lead_time_days", 0), item.get("safety_stock", 0),
        review_days, item.get("pack_size", 1), item.get("min_order", 0), on_order,
    ) if low else 0
    return {
        "sku": item.get("sku"),
        "on_hand": on_hand,
        "on_order": on_order,
        "avg_daily_use": adu,
        "reorder_point": rop,
        "days_of_cover": days_of_cover(on_hand, adu),
        "below_reorder_point": low,
        "suggested_qty": qty,
        "consumed_in_window": consumption_in_window(movements, as_of, window_days),
        "window_days": window_days,
    }
