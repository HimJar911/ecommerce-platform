"""
inventory_service — Stock management, reservations, warehouse sync

Manages stock levels across 6 warehouse nodes.
Redis cache for real-time reservation state.
Postgres as source of truth for committed stock levels.

Called by: order_service
Calls: warehouse nodes (internal), sync job (internal)

CRITICAL: INVENTORY_RESERVE_TIMEOUT controls how long reservations live.
If this is set too low (< order processing P99 of 6.2s), reservations
expire mid-order causing "item unavailable" errors even when stock exists.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from config.settings import (
    INVENTORY_RESERVE_TIMEOUT,
    INVENTORY_SYNC_INTERVAL,
    INVENTORY_LOW_STOCK_THRESHOLD,
    INVENTORY_MAX_CONCURRENT_RESERVATIONS,
    REDIS_HOST,
    REDIS_PORT,
    WEB_CONCURRENCY,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="EcomCore Inventory Service", version="3.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── In-memory store ──────────────────────────────────────────────────────────
_stock: dict[str, int] = {
    "SKU-001": 5000, "SKU-002": 1200, "SKU-003": 8750,
    "SKU-004": 340,  "SKU-005": 15600, "SKU-006": 2890,
    "SKU-007": 450,  "SKU-008": 9100,  "SKU-009": 67,
    "SKU-010": 3400,
}
_reservations: dict[str, dict] = {}
_reservation_counts: dict[str, int] = {}  # SKU → active reservation count


class ReservationRequest(BaseModel):
    order_id: str
    items: list[dict]  # [{sku, quantity}]


class StockUpdateRequest(BaseModel):
    sku: str
    delta: int  # positive = restock, negative = fulfill


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "inventory-service",
        "version": "3.1.0",
        "sync_interval": INVENTORY_SYNC_INTERVAL,
        "reserve_timeout": INVENTORY_RESERVE_TIMEOUT,
    }


@app.post("/reservations")
def create_reservation(request: ReservationRequest):
    """
    Reserve stock for an order.
    Reservation held for INVENTORY_RESERVE_TIMEOUT seconds.
    Returns reservation_id on success, 409 if insufficient stock.
    """
    # Validate all items have sufficient stock before reserving any
    for item in request.items:
        sku = item["sku"]
        qty = item["quantity"]
        available = _stock.get(sku, 0) - sum(
            r["quantity"] for r in _reservations.values()
            if r["sku"] == sku and r["status"] == "active"
        )
        if available < qty:
            logger.warning(f"[inventory] Insufficient stock for {sku}: need {qty}, have {available}")
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for SKU {sku}: requested {qty}, available {available}"
            )

        # Check concurrent reservation limit
        active_count = _reservation_counts.get(sku, 0)
        if active_count >= INVENTORY_MAX_CONCURRENT_RESERVATIONS:
            raise HTTPException(
                status_code=429,
                detail=f"Too many concurrent reservations for SKU {sku}"
            )

    # All checks passed — create reservations
    reservation_id = f"RES-{uuid.uuid4().hex[:12].upper()}"
    now = datetime.now(timezone.utc).isoformat()

    for item in request.items:
        sku = item["sku"]
        qty = item["quantity"]
        res_key = f"{reservation_id}:{sku}"

        _reservations[res_key] = {
            "reservation_id": reservation_id,
            "order_id": request.order_id,
            "sku": sku,
            "quantity": qty,
            "status": "active",
            "created_at": now,
            "expires_at": INVENTORY_RESERVE_TIMEOUT,
        }
        _reservation_counts[sku] = _reservation_counts.get(sku, 0) + 1

    logger.info(
        f"[inventory] Reservation {reservation_id} created for order {request.order_id}: "
        f"{len(request.items)} SKUs, timeout={INVENTORY_RESERVE_TIMEOUT}s"
    )

    return {
        "reservation_id": reservation_id,
        "order_id": request.order_id,
        "status": "active",
        "expires_in_seconds": INVENTORY_RESERVE_TIMEOUT,
        "created_at": now,
    }


@app.delete("/reservations/{reservation_id}")
def release_reservation(reservation_id: str):
    """Release a reservation (order cancelled or payment failed)."""
    released = []
    for key in list(_reservations.keys()):
        if _reservations[key]["reservation_id"] == reservation_id:
            sku = _reservations[key]["sku"]
            _reservations[key]["status"] = "released"
            _reservation_counts[sku] = max(0, _reservation_counts.get(sku, 1) - 1)
            released.append(sku)

    if not released:
        raise HTTPException(status_code=404, detail="Reservation not found")

    logger.info(f"[inventory] Released reservation {reservation_id}: {released}")
    return {"reservation_id": reservation_id, "status": "released", "skus": released}


@app.get("/stock/{sku}")
def get_stock(sku: str):
    """Get current stock level for a SKU."""
    if sku not in _stock:
        raise HTTPException(status_code=404, detail=f"SKU {sku} not found")

    total = _stock[sku]
    reserved = sum(
        r["quantity"] for r in _reservations.values()
        if r["sku"] == sku and r["status"] == "active"
    )
    available = total - reserved

    return {
        "sku": sku,
        "total": total,
        "reserved": reserved,
        "available": available,
        "low_stock": available <= INVENTORY_LOW_STOCK_THRESHOLD,
    }


@app.post("/stock/adjust")
def adjust_stock(request: StockUpdateRequest):
    """Adjust stock level (fulfillment or restock)."""
    if request.sku not in _stock:
        _stock[request.sku] = 0
    _stock[request.sku] = max(0, _stock[request.sku] + request.delta)
    return {"sku": request.sku, "new_level": _stock[request.sku]}


@app.get("/stock")
def list_stock():
    """List all stock levels."""
    result = {}
    for sku, total in _stock.items():
        reserved = sum(
            r["quantity"] for r in _reservations.values()
            if r["sku"] == sku and r["status"] == "active"
        )
        result[sku] = {
            "total": total,
            "reserved": reserved,
            "available": total - reserved,
            "low_stock": (total - reserved) <= INVENTORY_LOW_STOCK_THRESHOLD,
        }
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003, workers=WEB_CONCURRENCY)
