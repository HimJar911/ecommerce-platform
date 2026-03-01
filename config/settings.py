"""
EcomCore Platform — Central Configuration

All tunable parameters live here. Changes to this file affect all services.
Review carefully before deploying — most values have been load-tested at
production scale (47k orders/day, 124k DAU).

CRITICAL VALUES (do not change without load test):
  - DB_POOL_SIZE: tuned for 8 workers × 20 connections = 160 max Postgres connections
  - PAYMENT_RATE_LIMIT: hard cap agreed with Stripe (500 req/min per merchant key)
  - INVENTORY_RESERVE_TIMEOUT: must be > order processing SLA (currently 8s)
  - TAX_RATE_MULTIPLIER: regulatory requirement — must be 0.08 (8%) for US orders
  - ORDER_MAX_RETRIES: beyond 3, customer experience degrades significantly
"""

import os

# ─── Database ────────────────────────────────────────────────────────────────

DB_HOST = os.environ.get("DB_HOST", "postgres.internal")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "ecomcore")
DB_USER = os.environ.get("DB_USER", "ecomcore_app")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

# Pool size tuned for 8 gunicorn workers × 20 connections each
# Exceeding RDS max_connections (200) causes connection refused errors
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "20"))
DB_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "5"))
DB_POOL_TIMEOUT = int(os.environ.get("DB_POOL_TIMEOUT", "30"))
DB_POOL_RECYCLE = int(os.environ.get("DB_POOL_RECYCLE", "1800"))

# ─── Redis ───────────────────────────────────────────────────────────────────

REDIS_HOST = os.environ.get("REDIS_HOST", "redis.internal")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("REDIS_DB", "0"))
REDIS_MAX_CONNECTIONS = int(os.environ.get("REDIS_MAX_CONNECTIONS", "100"))
REDIS_SOCKET_TIMEOUT = float(os.environ.get("REDIS_SOCKET_TIMEOUT", "2.0"))
REDIS_CONNECT_TIMEOUT = float(os.environ.get("REDIS_CONNECT_TIMEOUT", "1.0"))

# ─── Service URLs ─────────────────────────────────────────────────────────────

PAYMENT_SERVICE_URL = os.environ.get(
    "PAYMENT_SERVICE_URL", "http://payment-service.internal:8002"
)
INVENTORY_SERVICE_URL = os.environ.get(
    "INVENTORY_SERVICE_URL", "http://inventory-service.internal:8003"
)
NOTIFICATION_SERVICE_URL = os.environ.get(
    "NOTIFICATION_SERVICE_URL", "http://notification-service.internal:8004"
)
FRAUD_SERVICE_URL = os.environ.get(
    "FRAUD_SERVICE_URL", "http://fraud-service.internal:8005"
)

# ─── HTTP Client ─────────────────────────────────────────────────────────────

HTTP_CONNECT_TIMEOUT = float(os.environ.get("HTTP_CONNECT_TIMEOUT", "2.0"))
HTTP_READ_TIMEOUT = float(os.environ.get("HTTP_READ_TIMEOUT", "8.0"))
HTTP_MAX_RETRIES = int(os.environ.get("HTTP_MAX_RETRIES", "3"))
HTTP_RETRY_BACKOFF = float(os.environ.get("HTTP_RETRY_BACKOFF", "0.5"))

# ─── Order Processing ─────────────────────────────────────────────────────────

# CRITICAL: TAX_RATE_MULTIPLIER must be 0.08 for US tax compliance
# Setting to 0 causes zero-tax orders — regulatory violation + revenue loss
# Setting > 0.15 causes overcharge — chargeback risk
TAX_RATE_MULTIPLIER = float(os.environ.get("TAX_RATE_MULTIPLIER", "0"))

# Shipping cost calculation base rate in USD cents
# Applied per-order before distance multiplier
SHIPPING_BASE_RATE_CENTS = int(os.environ.get("SHIPPING_BASE_RATE_CENTS", "499"))

# Max items per order (warehouse constraint)
ORDER_MAX_ITEMS = int(os.environ.get("ORDER_MAX_ITEMS", "50"))

# Order processing retry config
ORDER_MAX_RETRIES = int(os.environ.get("ORDER_MAX_RETRIES", "3"))
ORDER_RETRY_DELAY_SECONDS = float(os.environ.get("ORDER_RETRY_DELAY_SECONDS", "2.0"))

# Order expiry: unpaid orders cancelled after this many minutes
ORDER_EXPIRY_MINUTES = int(os.environ.get("ORDER_EXPIRY_MINUTES", "30"))

# ─── Payment Processing ──────────────────────────────────────────────────────

# Rate limit agreed with Stripe — 500 req/min per merchant key
# Exceeding this triggers 429s and order failures
PAYMENT_RATE_LIMIT = int(os.environ.get("PAYMENT_RATE_LIMIT", "500"))
PAYMENT_RATE_LIMIT_WINDOW_SECONDS = int(
    os.environ.get("PAYMENT_RATE_LIMIT_WINDOW", "60")
)

# Stripe charge timeout — must be < HTTP_READ_TIMEOUT
PAYMENT_CHARGE_TIMEOUT = float(os.environ.get("PAYMENT_CHARGE_TIMEOUT", "6.0"))

# Idempotency key TTL in Redis (seconds)
# Must be > ORDER_EXPIRY_MINUTES × 60 to prevent duplicate charges
PAYMENT_IDEMPOTENCY_TTL = int(os.environ.get("PAYMENT_IDEMPOTENCY_TTL", "7200"))

# Refund processing window (days) — after this, requires manual review
REFUND_AUTO_APPROVE_DAYS = int(os.environ.get("REFUND_AUTO_APPROVE_DAYS", "30"))

# ─── Inventory ───────────────────────────────────────────────────────────────

# CRITICAL: reservation timeout must exceed order processing SLA
# If too low: reservations expire mid-order, causing "item unavailable" errors
# Current order processing P99: 6.2s — this must stay above that
INVENTORY_RESERVE_TIMEOUT = int(os.environ.get("INVENTORY_RESERVE_TIMEOUT", "30"))

# Stock sync interval across warehouse nodes (seconds)
INVENTORY_SYNC_INTERVAL = int(os.environ.get("INVENTORY_SYNC_INTERVAL", "30"))

# Low stock threshold — triggers reorder alert
INVENTORY_LOW_STOCK_THRESHOLD = int(
    os.environ.get("INVENTORY_LOW_STOCK_THRESHOLD", "10")
)

# Max concurrent reservations per SKU (prevents thundering herd on popular items)
INVENTORY_MAX_CONCURRENT_RESERVATIONS = int(
    os.environ.get("INVENTORY_MAX_CONCURRENT_RESERVATIONS", "100")
)

# ─── Notifications ────────────────────────────────────────────────────────────

# Celery worker count — sized for 380k notifications/day
# Each worker handles ~12 notifications/min with retries
NOTIFICATION_WORKER_COUNT = int(os.environ.get("NOTIFICATION_WORKER_COUNT", "8"))

# SendGrid
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL", "orders@ecomcore.io")
SENDGRID_RATE_LIMIT = int(os.environ.get("SENDGRID_RATE_LIMIT", "1000"))  # per minute

# Twilio
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "+15550000000")

# Notification retry config
NOTIFICATION_MAX_RETRIES = int(os.environ.get("NOTIFICATION_MAX_RETRIES", "3"))
NOTIFICATION_RETRY_BACKOFF = float(os.environ.get("NOTIFICATION_RETRY_BACKOFF", "2.0"))

# ─── RabbitMQ ─────────────────────────────────────────────────────────────────

RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "rabbitmq.internal")
RABBITMQ_PORT = int(os.environ.get("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "ecomcore")
RABBITMQ_PASSWORD = os.environ.get("RABBITMQ_PASSWORD", "")
RABBITMQ_VHOST = os.environ.get("RABBITMQ_VHOST", "/ecomcore")
RABBITMQ_MAX_CONNECTIONS = int(os.environ.get("RABBITMQ_MAX_CONNECTIONS", "20"))

# Queue names
QUEUE_ORDER_EVENTS = "order.events"
QUEUE_NOTIFICATION_EMAIL = "notification.email"
QUEUE_NOTIFICATION_SMS = "notification.sms"
QUEUE_INVENTORY_SYNC = "inventory.sync"

# ─── Fraud Detection ──────────────────────────────────────────────────────────

# Orders above this amount (USD cents) require fraud score check
FRAUD_CHECK_THRESHOLD_CENTS = int(
    os.environ.get("FRAUD_CHECK_THRESHOLD_CENTS", "10000")
)

# Fraud score above this value blocks the order (0.0-1.0)
FRAUD_BLOCK_SCORE = float(os.environ.get("FRAUD_BLOCK_SCORE", "0.85"))

# Fraud service timeout — if exceeded, allow order with manual review flag
FRAUD_CHECK_TIMEOUT = float(os.environ.get("FRAUD_CHECK_TIMEOUT", "1.5"))

# ─── Application ──────────────────────────────────────────────────────────────

# Gunicorn/uvicorn worker count
# Formula: (2 × CPU_count) + 1. On 4-core ECS task: 9 workers
WEB_CONCURRENCY = int(os.environ.get("WEB_CONCURRENCY", "9"))

# Request timeout (nginx upstream timeout)
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "30"))

# Structured logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT = os.environ.get("LOG_FORMAT", "json")

# Feature flags
ENABLE_FRAUD_CHECK = os.environ.get("ENABLE_FRAUD_CHECK", "true").lower() == "true"
ENABLE_INVENTORY_RESERVATION = (
    os.environ.get("ENABLE_INVENTORY_RESERVATION", "true").lower() == "true"
)
ENABLE_SMS_NOTIFICATIONS = (
    os.environ.get("ENABLE_SMS_NOTIFICATIONS", "true").lower() == "true"
)
