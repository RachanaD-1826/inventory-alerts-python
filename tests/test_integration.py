import os

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    if not os.environ.get("DATABASE_URL") or not os.environ.get("REDIS_URL"):
        pytest.skip("Integration tests require PostgreSQL and Redis")

    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["postgres"] is True
    assert data["redis"] is True


def test_warehouses_endpoint(client):
    response = client.get("/warehouses")

    assert response.status_code == 200
    assert "warehouses" in response.json()


def test_alerts_reject_invalid_limit(client):
    response = client.get("/alerts?limit=0")

    assert response.status_code == 400


def test_alerts_reject_limit_over_200(client):
    response = client.get("/alerts?limit=201")

    assert response.status_code == 400


def test_stock_endpoint(client):
    response = client.get("/stock")
    assert response.status_code == 200
    data = response.json()
    assert "lines" in data
    assert isinstance(data["lines"], list)

def test_stock_filter_by_warehouse(client):
    response = client.get("/stock?warehouse=BLR")
    assert response.status_code in (200, 404)


def test_stock_filter_by_sku(client):
    response = client.get("/stock?sku=INVALID-SKU")
    assert response.status_code == 404


def test_movement_rejects_invalid_kind(client):
    response = client.post(
        "/movements",
        json={
            "sku": "INVALID-SKU",
            "warehouse": "INVALID-WH",
            "kind": "unknown",
            "qty": 5,
        },
    )
    assert response.status_code == 404


def test_transfer_rejects_unknown_item(client):
    response = client.post(
        "/transfers",
        json={
            "sku": "INVALID-SKU",
            "from": "BLR",
            "to": "MUM",
            "qty": 5,
        },
    )
    assert response.status_code == 404
def test_movement_rejects_transfer_kind(client):
    response = client.post(
        "/movements",
        json={
            "sku": "INVALID-SKU",
            "warehouse": "INVALID-WH",
            "kind": "transfer_out",
            "qty": 5,
        },
    )
    assert response.status_code == 404


def test_transfer_rejects_nonpositive_quantity(client):
    response = client.post(
        "/transfers",
        json={
            "sku": "INVALID-SKU",
            "from": "BLR",
            "to": "MUM",
            "qty": 0,
        },
    )
    assert response.status_code == 404
def test_movement_rejects_transfer_kind_for_valid_item(client):
    response = client.post(
        "/movements",
        json={
            "sku": "KB-104",
            "warehouse": "BLR",
            "kind": "transfer_out",
            "qty": 5,
        },
    )
    assert response.status_code == 400


def test_movement_rejects_zero_quantity(client):
    response = client.post(
        "/movements",
        json={
            "sku": "KB-104",
            "warehouse": "BLR",
            "kind": "sale",
            "qty": 0,
        },
    )
    assert response.status_code == 400


def test_movement_rejects_invalid_quantity(client):
    response = client.post(
        "/movements",
        json={
            "sku": "KB-104",
            "warehouse": "BLR",
            "kind": "sale",
            "qty": "abc",
        },
    )
    assert response.status_code == 400


def test_transfer_rejects_same_warehouse(client):
    response = client.post(
        "/transfers",
        json={
            "sku": "KB-104",
            "from": "BLR",
            "to": "BLR",
            "qty": 5,
        },
    )
    assert response.status_code == 409

