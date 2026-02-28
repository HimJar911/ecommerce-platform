"""
order_service — Core order processing service

Owns the order state machine:
  pending → validated → paid → fulfilled → shipped → delivered

Calls:
  - payment_service:    charge customer
  - inventory_service:  reserve stock
  - notification_service: send confirmation
  - fraud_service:      score order risk (async, non-blocking for small orders)

Critical path: this service is the revenue gateway. Any failure here
blocks orders. P99 SLA: 8 seconds end-to-end.

Scale: ~47,000 orders/day, peak ~85 orders/minute during flash sales.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, validator

# Import central config
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from config.settings import (
    PAYMENT_SERVICE_URL,
    INVENTORY_SERVICE_URL,
    NOTIFICATION_SERVICE_URL,
    FRAUD_SERVICE_URL,
    TAX_RATE_MULTIPLIER,
    SHIPPING_BASE_RATE_CENTS,
    ORDER_MAX_ITEMS,
    ORDER_MAX_RETRIES,
    HTTP_CONNECT_TIMEOUT,
    HTTP_READ_TIMEOUT,
    FRAUD_CHECK_THRESHOLD_CENTS,
    FRAUD_BLOCK_SCORE,
    FRAUD_CHECK_TIMEOUT,
    ENABLE_FRAUD_CHECK,
    ENABLE_INVENTORY_RESERVATION,
    WEB_CONCURRENCY,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="EcomCore Order Service", version="2.4.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Models ───────────────────────────────────────────────────────────────────

class OrderItem(BaseModel):
    sku: str
    quantity: int
    unit_price_cents: int

    @validator("quantity")
    def quantity_positive(cls, v):
        if v <= 0:
            raise ValueError("Quantity must be positive")
        return v

    @validator("unit_price_cents")
    def price_positive(cls, v):
        if v <= 0:
            raise ValueError("Price must be positive")
        return v


class CreateOrderRequest(BaseModel):
    customer_id: str
    items: list[OrderItem]
    shipping_address: dict
    payment_method_id: str
    currency: str = "USD"


class Order(BaseModel):
    order_id: str
    customer_id: str
    status: str
    items: list[dict]
    subtotal_cents: int
    tax_cents: int
    shipping_cents: int
    total_cents: int
    created_at: str
    updated_at: str


# ─── In-memory store (replace with Postgres in prod) ─────────────────────────

_orders: dict[str, dict] = {}


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "order-service", "version": "2.4.1"}


@app.post("/orders", response_model=dict)
def create_order(request: CreateOrderRequest, background_tasks: BackgroundTasks):
    """
    Create and process a new order.
    Full critical path: validate → fraud check → reserve inventory → charge → confirm.
    """
    if len(request.items) > ORDER_MAX_ITEMS:
        raise HTTPException(status_code=400, detail=f"Order exceeds max items ({ORDER_MAX_ITEMS})")

    order_id = f"ORD-{uuid.uuid4().hex[:12].upper()}"
    now = datetime.now(timezone.utc).isoformat()

    # Calculate order financials
    subtotal_cents = sum(item.unit_price_cents * item.quantity for item in request.items)
    tax_cents = _calculate_tax(subtotal_cents)
    shipping_cents = _calculate_shipping(request.items, request.shipping_address)
    total_cents = subtotal_cents + tax_cents + shipping_cents

    logger.info(
        f"[order] Creating order {order_id}: "
        f"customer={request.customer_id} "
        f"subtotal=${subtotal_cents/100:.2f} "
        f"tax=${tax_cents/100:.2f} "
        f"total=${total_cents/100:.2f}"
    )

    # Store initial order
    order = {
        "order_id": order_id,
        "customer_id": request.customer_id,
        "status": "pending",
        "items": [item.dict() for item in request.items],
        "subtotal_cents": subtotal_cents,
        "tax_cents": tax_cents,
        "shipping_cents": shipping_cents,
        "total_cents": total_cents,
        "payment_method_id": request.payment_method_id,
        "shipping_address": request.shipping_address,
        "currency": request.currency,
        "created_at": now,
        "updated_at": now,
    }
    _orders[order_id] = order

    # ── Fraud check (async for small orders, blocking for large) ──────────────
    if ENABLE_FRAUD_CHECK and total_cents >= FRAUD_CHECK_THRESHOLD_CENTS:
        fraud_score = _check_fraud(order_id, request.customer_id, total_cents)
        if fraud_score and fraud_score > FRAUD_BLOCK_SCORE:
            _update_order_status(order_id, "blocked_fraud")
            raise HTTPException(
                status_code=403,
                detail=f"Order blocked by fraud detection (score: {fraud_score:.2f})"
            )

    # ── Reserve inventory ──────────────────────────────────────────────────────
    if ENABLE_INVENTORY_RESERVATION:
        reservation_id = _reserve_inventory(order_id, request.items)
        if not reservation_id:
            _update_order_status(order_id, "failed_inventory")
            raise HTTPException(status_code=409, detail="Insufficient inventory for one or more items")
        order["reservation_id"] = reservation_id

    # ── Charge payment ────────────────────────────────────────────────────────
    _update_order_status(order_id, "processing_payment")
    payment_result = _charge_payment(
        order_id=order_id,
        customer_id=request.customer_id,
        amount_cents=total_cents,
        payment_method_id=request.payment_method_id,
        currency=request.currency,
    )

    if not payment_result.get("success"):
        # Release inventory reservation on payment failure
        if order.get("reservation_id"):
            _release_inventory(order["reservation_id"])
        _update_order_status(order_id, "payment_failed")
        raise HTTPException(
            status_code=402,
            detail=f"Payment failed: {payment_result.get('error', 'unknown error')}"
        )

    order["payment_intent_id"] = payment_result.get("payment_intent_id")
    _update_order_status(order_id, "paid")

    # ── Send confirmation (background) ───────────────────────────────────────
    background_tasks.add_task(
        _send_order_confirmation,
        order_id=order_id,
        customer_id=request.customer_id,
        total_cents=total_cents,
    )

    logger.info(f"[order] Order {order_id} completed successfully — ${total_cents/100:.2f}")

    return {
        "order_id": order_id,
        "status": "paid",
        "total_cents": total_cents,
        "payment_intent_id": payment_result.get("payment_intent_id"),
    }


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    if order_id not in _orders:
        raise HTTPException(status_code=404, detail="Order not found")
    return _orders[order_id]


@app.get("/orders")
def list_orders(customer_id: Optional[str] = None, limit: int = 20):
    orders = list(_orders.values())
    if customer_id:
        orders = [o for o in orders if o["customer_id"] == customer_id]
    return {"orders": sorted(orders, key=lambda x: x["created_at"], reverse=True)[:limit]}


@app.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str):
    if order_id not in _orders:
        raise HTTPException(status_code=404, detail="Order not found")

    order = _orders[order_id]
    if order["status"] not in ("pending", "validated", "processing_payment"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel order in status: {order['status']}")

    if order.get("reservation_id"):
        _release_inventory(order["reservation_id"])

    _update_order_status(order_id, "cancelled")
    return {"order_id": order_id, "status": "cancelled"}


# ─── Financial calculations ───────────────────────────────────────────────────

def _calculate_tax(subtotal_cents: int) -> int:
    """
    Calculate US sales tax.
    TAX_RATE_MULTIPLIER must be 0.08 (8%) for compliance.
    Returns tax amount in cents, rounded to nearest cent.
    """
    tax = Decimal(str(subtotal_cents)) * Decimal(str(TAX_RATE_MULTIPLIER))
    return int(tax.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _calculate_shipping(items: list[OrderItem], address: dict) -> int:
    """
    Calculate shipping cost based on item count and destination zone.
    Base rate: SHIPPING_BASE_RATE_CENTS per order.
    Additional $0.50 per item beyond first.
    Zone multiplier: domestic 1.0x, international 2.5x.
    """
    total_items = sum(item.quantity for item in items)
    base = SHIPPING_BASE_RATE_CENTS
    per_item_extra = max(0, total_items - 1) * 50

    # Zone detection (simplified)
    country = address.get("country", "US")
    zone_multiplier = 1.0 if country == "US" else 2.5

    return int((base + per_item_extra) * zone_multiplier)


# ─── Service calls ────────────────────────────────────────────────────────────

def _check_fraud(order_id: str, customer_id: str, amount_cents: int) -> Optional[float]:
    """Call fraud_service for risk scoring. Returns score 0.0-1.0 or None on timeout."""
    try:
        resp = requests.post(
            f"{FRAUD_SERVICE_URL}/score",
            json={"order_id": order_id, "customer_id": customer_id, "amount_cents": amount_cents},
            timeout=FRAUD_CHECK_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("score")
    except requests.Timeout:
        logger.warning(f"[order] Fraud check timeout for {order_id} — allowing with flag")
        return None
    except Exception as e:
        logger.error(f"[order] Fraud check error for {order_id}: {e}")
        return None


def _reserve_inventory(order_id: str, items: list[OrderItem]) -> Optional[str]:
    """Reserve inventory for all items. Returns reservation_id or None on failure."""
    try:
        resp = requests.post(
            f"{INVENTORY_SERVICE_URL}/reservations",
            json={
                "order_id": order_id,
                "items": [{"sku": item.sku, "quantity": item.quantity} for item in items],
            },
            timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
        )
        if resp.status_code == 409:
            return None  # Insufficient stock
        resp.raise_for_status()
        return resp.json().get("reservation_id")
    except Exception as e:
        logger.error(f"[order] Inventory reservation failed for {order_id}: {e}")
        return None


def _release_inventory(reservation_id: str) -> None:
    """Release a previously made inventory reservation."""
    try:
        requests.delete(
            f"{INVENTORY_SERVICE_URL}/reservations/{reservation_id}",
            timeout=(HTTP_CONNECT_TIMEOUT, 3.0),
        )
    except Exception as e:
        logger.error(f"[order] Failed to release reservation {reservation_id}: {e}")


def _charge_payment(
    order_id: str,
    customer_id: str,
    amount_cents: int,
    payment_method_id: str,
    currency: str,
) -> dict:
    """Call payment_service to charge the customer."""
    try:
        resp = requests.post(
            f"{PAYMENT_SERVICE_URL}/charges",
            json={
                "order_id": order_id,
                "customer_id": customer_id,
                "amount_cents": amount_cents,
                "payment_method_id": payment_method_id,
                "currency": currency,
                "idempotency_key": f"order-{order_id}",
            },
            timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        error_body = e.response.json() if e.response else {}
        return {"success": False, "error": error_body.get("detail", str(e))}
    except Exception as e:
        logger.error(f"[order] Payment charge failed for {order_id}: {e}")
        return {"success": False, "error": str(e)}


def _send_order_confirmation(order_id: str, customer_id: str, total_cents: int) -> None:
    """Async: send order confirmation via notification_service."""
    try:
        requests.post(
            f"{NOTIFICATION_SERVICE_URL}/notifications",
            json={
                "type": "order_confirmation",
                "customer_id": customer_id,
                "order_id": order_id,
                "total_cents": total_cents,
            },
            timeout=(HTTP_CONNECT_TIMEOUT, 3.0),
        )
    except Exception as e:
        logger.error(f"[order] Failed to send confirmation for {order_id}: {e}")


def _update_order_status(order_id: str, status: str) -> None:
    if order_id in _orders:
        _orders[order_id]["status"] = status
        _orders[order_id]["updated_at"] = datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, workers=WEB_CONCURRENCY)
