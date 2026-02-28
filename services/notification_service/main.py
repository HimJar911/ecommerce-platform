"""
notification_service — Email/SMS dispatch

Async notification delivery via Celery workers.
SendGrid for transactional email, Twilio for SMS.
Backed by RabbitMQ queue — decoupled from order critical path.

Called by: order_service (fire-and-forget)
Calls: SendGrid API (external), Twilio API (external)

Scale: ~380,000 notifications/day (email + SMS combined).
8 Celery workers each processing ~12 notifications/minute with retries.
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
    SENDGRID_API_KEY,
    SENDGRID_FROM_EMAIL,
    SENDGRID_RATE_LIMIT,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_FROM_NUMBER,
    NOTIFICATION_MAX_RETRIES,
    NOTIFICATION_RETRY_BACKOFF,
    NOTIFICATION_WORKER_COUNT,
    RABBITMQ_HOST,
    RABBITMQ_PORT,
    ENABLE_SMS_NOTIFICATIONS,
    WEB_CONCURRENCY,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="EcomCore Notification Service", version="2.0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_notifications: dict[str, dict] = {}
_delivery_stats = {"sent": 0, "failed": 0, "retried": 0}

NOTIFICATION_TEMPLATES = {
    "order_confirmation": {
        "subject": "Your order has been confirmed",
        "body": "Thank you for your order! Your order #{order_id} totaling ${total:.2f} is confirmed.",
    },
    "order_shipped": {
        "subject": "Your order has shipped",
        "body": "Good news! Order #{order_id} has shipped. Track: {tracking_url}",
    },
    "order_cancelled": {
        "subject": "Order cancellation confirmed",
        "body": "Your order #{order_id} has been cancelled. Refund processing in 3-5 days.",
    },
    "low_stock_alert": {
        "subject": "[ALERT] Low stock warning",
        "body": "SKU {sku} is running low: {available} units remaining.",
    },
    "payment_failed": {
        "subject": "Action required: Payment failed",
        "body": "We couldn't process payment for order #{order_id}. Please update your payment method.",
    },
}


class NotificationRequest(BaseModel):
    type: str
    customer_id: str
    order_id: Optional[str] = None
    total_cents: Optional[int] = None
    metadata: dict = {}
    channel: str = "email"  # email | sms | both


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "notification-service",
        "version": "2.0.1",
        "worker_count": NOTIFICATION_WORKER_COUNT,
        "stats": _delivery_stats,
    }


@app.post("/notifications")
def send_notification(request: NotificationRequest):
    """
    Enqueue a notification for delivery.
    In production: pushes to RabbitMQ queue for Celery workers.
    """
    if request.type not in NOTIFICATION_TEMPLATES:
        raise HTTPException(status_code=400, detail=f"Unknown notification type: {request.type}")

    notification_id = f"NOTIF-{uuid.uuid4().hex[:12].upper()}"
    template = NOTIFICATION_TEMPLATES[request.type]

    # Format template
    format_vars = {
        "order_id": request.order_id or "N/A",
        "total": (request.total_cents or 0) / 100,
        "tracking_url": request.metadata.get("tracking_url", "#"),
        "sku": request.metadata.get("sku", ""),
        "available": request.metadata.get("available", 0),
    }

    notification = {
        "notification_id": notification_id,
        "type": request.type,
        "customer_id": request.customer_id,
        "order_id": request.order_id,
        "channel": request.channel,
        "subject": template["subject"],
        "body": template["body"].format(**format_vars),
        "status": "queued",
        "attempts": 0,
        "max_retries": NOTIFICATION_MAX_RETRIES,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "delivered_at": None,
    }

    _notifications[notification_id] = notification

    # Simulate delivery
    _deliver_notification(notification_id)

    return {
        "notification_id": notification_id,
        "status": notification["status"],
        "channel": request.channel,
    }


@app.get("/notifications/{notification_id}")
def get_notification(notification_id: str):
    if notification_id not in _notifications:
        raise HTTPException(status_code=404, detail="Notification not found")
    return _notifications[notification_id]


@app.get("/notifications/stats")
def get_stats():
    return _delivery_stats


def _deliver_notification(notification_id: str) -> None:
    """Simulate delivery. In production: Celery worker picks this up from RabbitMQ."""
    notif = _notifications[notification_id]

    channel = notif["channel"]
    try:
        if channel in ("email", "both"):
            _send_email(
                to_customer_id=notif["customer_id"],
                subject=notif["subject"],
                body=notif["body"],
            )
        if channel in ("sms", "both") and ENABLE_SMS_NOTIFICATIONS:
            _send_sms(
                to_customer_id=notif["customer_id"],
                message=notif["body"][:160],
            )

        _notifications[notification_id]["status"] = "delivered"
        _notifications[notification_id]["delivered_at"] = datetime.now(timezone.utc).isoformat()
        _delivery_stats["sent"] += 1
        logger.info(f"[notification] Delivered {notification_id} via {channel}")

    except Exception as e:
        _notifications[notification_id]["attempts"] += 1
        _delivery_stats["failed"] += 1
        logger.error(f"[notification] Failed to deliver {notification_id}: {e}")

        if _notifications[notification_id]["attempts"] < NOTIFICATION_MAX_RETRIES:
            _notifications[notification_id]["status"] = "retrying"
            _delivery_stats["retried"] += 1
        else:
            _notifications[notification_id]["status"] = "failed"


def _send_email(to_customer_id: str, subject: str, body: str) -> None:
    """SendGrid integration (simulated)."""
    if not SENDGRID_API_KEY:
        logger.debug(f"[notification] SendGrid not configured — simulating email to {to_customer_id}")
        return
    # In production: POST https://api.sendgrid.com/v3/mail/send
    logger.info(f"[notification] Email sent to customer {to_customer_id}: {subject}")


def _send_sms(to_customer_id: str, message: str) -> None:
    """Twilio integration (simulated)."""
    if not TWILIO_ACCOUNT_SID:
        logger.debug(f"[notification] Twilio not configured — simulating SMS to {to_customer_id}")
        return
    # In production: POST https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages
    logger.info(f"[notification] SMS sent to customer {to_customer_id}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004, workers=WEB_CONCURRENCY)
