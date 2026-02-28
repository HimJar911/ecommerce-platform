"""
payment_service — Charge processing, refunds, ledger

Handles all money movement for EcomCore. Integrates with Stripe for
card processing. Maintains internal transaction ledger in Postgres.
Uses Redis for idempotency key storage (prevents duplicate charges).

Called by: order_service
Calls: Stripe API (external), fraud_service (internal)

Scale: ~47,000 charges/day, $2.3M daily volume.
Rate limited by Stripe: 500 req/min per merchant key.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from config.settings import (
    PAYMENT_RATE_LIMIT,
    PAYMENT_CHARGE_TIMEOUT,
    PAYMENT_IDEMPOTENCY_TTL,
    REFUND_AUTO_APPROVE_DAYS,
    REDIS_HOST,
    REDIS_PORT,
    WEB_CONCURRENCY,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="EcomCore Payment Service", version="1.8.3")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── In-memory stores ────────────────────────────────────────────────────────
_transactions: dict[str, dict] = {}
_idempotency_cache: dict[str, str] = {}  # key → transaction_id
_rate_limit_counter: dict[str, list] = {}  # merchant_id → [timestamps]


class ChargeRequest(BaseModel):
    order_id: str
    customer_id: str
    amount_cents: int
    payment_method_id: str
    currency: str = "USD"
    idempotency_key: str


class RefundRequest(BaseModel):
    transaction_id: str
    amount_cents: Optional[int] = None  # None = full refund
    reason: str = "customer_request"


@app.get("/health")
def health():
    return {"status": "ok", "service": "payment-service", "version": "1.8.3"}


@app.post("/charges")
def create_charge(request: ChargeRequest):
    """
    Process a payment charge.
    Idempotent: same idempotency_key returns existing transaction.
    """
    # Idempotency check
    if request.idempotency_key in _idempotency_cache:
        existing_id = _idempotency_cache[request.idempotency_key]
        logger.info(f"[payment] Idempotent charge — returning existing {existing_id}")
        return {**_transactions[existing_id], "idempotent": True}

    # Rate limit check
    if not _check_rate_limit(request.customer_id):
        raise HTTPException(status_code=429, detail="Payment rate limit exceeded")

    if request.amount_cents <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    transaction_id = f"TXN-{uuid.uuid4().hex[:12].upper()}"
    now = datetime.now(timezone.utc).isoformat()

    logger.info(
        f"[payment] Processing charge {transaction_id}: "
        f"order={request.order_id} "
        f"amount=${request.amount_cents/100:.2f} "
        f"currency={request.currency}"
    )

    # Simulate Stripe API call
    stripe_result = _call_stripe_charge(
        amount_cents=request.amount_cents,
        payment_method_id=request.payment_method_id,
        currency=request.currency,
        metadata={"order_id": request.order_id, "customer_id": request.customer_id},
    )

    transaction = {
        "transaction_id": transaction_id,
        "order_id": request.order_id,
        "customer_id": request.customer_id,
        "amount_cents": request.amount_cents,
        "currency": request.currency,
        "status": "succeeded" if stripe_result["success"] else "failed",
        "stripe_payment_intent_id": stripe_result.get("payment_intent_id"),
        "stripe_error": stripe_result.get("error"),
        "created_at": now,
        "idempotency_key": request.idempotency_key,
    }

    _transactions[transaction_id] = transaction
    if stripe_result["success"]:
        _idempotency_cache[request.idempotency_key] = transaction_id

    if not stripe_result["success"]:
        raise HTTPException(
            status_code=402,
            detail=f"Charge failed: {stripe_result.get('error', 'unknown')}"
        )

    return {
        "success": True,
        "transaction_id": transaction_id,
        "payment_intent_id": stripe_result.get("payment_intent_id"),
        "amount_cents": request.amount_cents,
    }


@app.post("/refunds")
def create_refund(request: RefundRequest):
    """Process a refund against an existing transaction."""
    if request.transaction_id not in _transactions:
        raise HTTPException(status_code=404, detail="Transaction not found")

    txn = _transactions[request.transaction_id]
    refund_amount = request.amount_cents or txn["amount_cents"]

    if refund_amount > txn["amount_cents"]:
        raise HTTPException(status_code=400, detail="Refund amount exceeds original charge")

    refund_id = f"REF-{uuid.uuid4().hex[:12].upper()}"
    logger.info(f"[payment] Processing refund {refund_id} for {request.transaction_id}")

    return {
        "refund_id": refund_id,
        "transaction_id": request.transaction_id,
        "amount_cents": refund_amount,
        "status": "succeeded",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/transactions/{transaction_id}")
def get_transaction(transaction_id: str):
    if transaction_id not in _transactions:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return _transactions[transaction_id]


def _check_rate_limit(customer_id: str) -> bool:
    """Simple sliding window rate limiter."""
    import time
    now = time.time()
    window = 60  # seconds

    if customer_id not in _rate_limit_counter:
        _rate_limit_counter[customer_id] = []

    # Remove old entries
    _rate_limit_counter[customer_id] = [
        t for t in _rate_limit_counter[customer_id] if now - t < window
    ]

    if len(_rate_limit_counter[customer_id]) >= PAYMENT_RATE_LIMIT:
        return False

    _rate_limit_counter[customer_id].append(now)
    return True


def _call_stripe_charge(
    amount_cents: int,
    payment_method_id: str,
    currency: str,
    metadata: dict,
) -> dict:
    """
    Stripe API integration.
    In production: calls api.stripe.com/v1/payment_intents
    In this demo: simulates Stripe response.
    """
    # Simulate: declined cards start with "pm_fail_"
    if payment_method_id.startswith("pm_fail_"):
        return {"success": False, "error": "card_declined"}

    # Simulate: test cards start with "pm_test_"
    payment_intent_id = f"pi_{uuid.uuid4().hex[:24]}"
    return {
        "success": True,
        "payment_intent_id": payment_intent_id,
        "status": "succeeded",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002, workers=WEB_CONCURRENCY)
