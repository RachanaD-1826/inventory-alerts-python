from datetime import date, timedelta

import pytest

from app.reorder import (
    StockError,
    average_daily_use,
    classify_movement,
    consumption_in_window,
    days_of_cover,
    evaluate,
    is_consumption,
    needs_reorder,
    reorder_point,
    round_to_pack,
    suggested_order_qty,
)

AS_OF = date(2026, 10, 9)


def movement(kind, delta, days_ago=1, transfer_id=None):
    return {
        "kind": kind,
        "delta": delta,
        "moved_at": AS_OF - timedelta(days=days_ago),
        "transfer_id": transfer_id,
    }


@pytest.mark.parametrize(
    "kind,delta,transfer_id,expected",
    [
        ("sale", -5, None, "consumption"),
        ("issue", -3, None, "consumption"),
        ("receipt", 10, None, "replenishment"),
        ("return", 2, None, "replenishment"),
        ("scrap", -2, None, "shrinkage"),
        ("damage", -1, None, "shrinkage"),
        ("transfer_out", -5, None, "transfer"),
        ("transfer_in", 5, None, "transfer"),
        ("sale", -5, "transfer-123", "transfer"),
        ("receipt", 5, "transfer-123", "transfer"),
        ("sale", 5, None, "consumption"),
        ("receipt", -5, None, "replenishment"),
    ],
)
def test_classify_movement(kind, delta, transfer_id, expected):
    assert classify_movement(kind, delta, transfer_id) == expected


def test_unknown_movement_kind_raises_error():
    with pytest.raises(StockError):
        classify_movement("unknown", -1)


@pytest.mark.parametrize(
    "kind,delta,transfer_id,expected",
    [
        ("sale", -5, None, True),
        ("issue", -3, None, True),
        ("scrap", -2, None, False),
        ("damage", -1, None, False),
        ("receipt", 10, None, False),
        ("transfer_out", -5, "transfer-123", False),
        ("sale", -5, "transfer-123", False),
        ("sale", 5, None, False),
    ],
)
def test_is_consumption(kind, delta, transfer_id, expected):
    item = movement(kind, delta, transfer_id=transfer_id)
    assert is_consumption(item) is expected


def test_consumption_window_boundaries():
    movements = [
        movement("sale", -100, days_ago=30),  # Exactly 30 days old: excluded
        movement("sale", -4, days_ago=29),    # Included
        movement("issue", -2, days_ago=1),    # Included
        movement("sale", -50, days_ago=31),   # Too old: excluded
        movement("sale", -20, days_ago=-1),   # Future: excluded
    ]

    assert consumption_in_window(movements, AS_OF, 30) == 6


def test_transfers_do_not_count_as_consumption():
    movements = [
        movement("transfer_out", -20, transfer_id="transfer-123"),
        movement("transfer_in", 20, transfer_id="transfer-123"),
    ]

    assert consumption_in_window(movements, AS_OF, 30) == 0
    assert average_daily_use(movements, AS_OF, 30) == 0.0


def test_no_movements_gives_zero_average():
    assert average_daily_use([], AS_OF, 30) == 0.0


def test_invalid_consumption_window_raises_error():
    with pytest.raises(StockError):
        consumption_in_window([], AS_OF, 0)


@pytest.mark.parametrize(
    "avg_daily_use,lead_time,safety_stock,expected",
    [
        (0, 0, 0, 0.0),
        (2.5, 0, 4, 4.0),
        (2.5, 4, 3, 13.0),
    ],
)
def test_reorder_point(avg_daily_use, lead_time, safety_stock, expected):
    assert reorder_point(avg_daily_use, lead_time, safety_stock) == expected


def test_negative_lead_time_raises_error():
    with pytest.raises(StockError):
        reorder_point(2, -1, 0)


def test_negative_safety_stock_raises_error():
    with pytest.raises(StockError):
        reorder_point(2, 1, -1)


@pytest.mark.parametrize(
    "qty,pack_size,expected",
    [
        (1, 12, 12),
        (12, 12, 12),
        (13, 12, 24),
        (0, 12, 0),
        (5, 1, 5),
        (5, 0, 5),
    ],
)
def test_round_to_pack(qty, pack_size, expected):
    assert round_to_pack(qty, pack_size) == expected


def test_days_of_cover_when_no_demand():
    assert days_of_cover(20, 0) is None


def test_days_of_cover_with_demand():
    assert days_of_cover(20, 2) == 10.0


def test_needs_reorder_at_boundary():
    assert needs_reorder(10, 10) is True
    assert needs_reorder(11, 10) is False
    assert needs_reorder(10, 10, on_order=1) is False


def test_suggested_order_accounts_for_stock_on_order():
    without_incoming = suggested_order_qty(0, 2, 5, review_days=7)
    with_incoming = suggested_order_qty(0, 2, 5, review_days=7, on_order=10)

    assert with_incoming < without_incoming


def test_suggested_order_is_never_negative():
    assert suggested_order_qty(100, 1, 5) == 0


def test_minimum_order_is_applied_before_pack_rounding():
    assert suggested_order_qty(
        0, 1, 1, min_order=13, pack_size=12
    ) == 24

def test_evaluate_with_transfers_only():
    item = {
        "sku": "TEST-001",
        "on_hand": 10,
        "lead_time_days": 7,
        "safety_stock": 0,
        "pack_size": 12,
        "min_order": 0,
        "on_order": 0,
    }
    movements = [
        movement("transfer_out", -20, transfer_id="transfer-123"),
        movement("transfer_in", 20, transfer_id="transfer-123"),
    ]

    result = evaluate(item, movements, AS_OF)

    assert result["avg_daily_use"] == 0.0
    assert result["consumed_in_window"] == 0
    assert result["days_of_cover"] is None
    assert result["below_reorder_point"] is False
    assert result["suggested_qty"] == 0
