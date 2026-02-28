# EcomCore Platform

Production e-commerce backend powering high-volume order processing across multiple fulfillment regions.

## Scale

- Handles ~47,000 orders per day across peak and off-peak windows
- Serves approximately 124,000 daily active users
- Processes $2.3M in transactions daily through the payment pipeline
- Inventory sync runs across 6 warehouse nodes with sub-200ms SLA
- Notification throughput: ~380,000 emails/SMS per day

## Architecture

Four core microservices communicating over internal HTTP:

```
Client
  └── order_service         (port 8001) — order lifecycle, validation, state machine
        ├── payment_service (port 8002) — charge processing, refunds, ledger
        ├── inventory_service (port 8003) — stock reservation, release, sync
        └── notification_service (port 8004) — email/SMS dispatch via SendGrid + Twilio
```

Each service is independently deployable. All inter-service calls use internal DNS
(`payment-service.internal`, `inventory-service.internal`, etc.).

## Services

### order_service
Owns the order state machine: `pending → validated → paid → fulfilled → shipped`.
Calls payment_service to charge, inventory_service to reserve stock, notification_service
to send confirmations. Critical path — any failure here blocks revenue.

### payment_service
Handles Stripe integration, internal ledger, refund processing, and fraud scoring.
Talks to external Stripe API and internal fraud_service. Maintains transaction log in
Postgres with Redis cache for idempotency keys. Rate-limited to 500 req/min per merchant.

### inventory_service
Manages stock levels across warehouse nodes. Uses Redis for real-time reservation cache
with Postgres as source of truth. Sync job runs every 30s. If reservation cache drifts,
overselling occurs — see RB-0003 for recovery.

### notification_service
Async email/SMS dispatch. Backed by Celery workers consuming from RabbitMQ.
SendGrid for email, Twilio for SMS. Retries up to 3x with exponential backoff.

## Tech Stack

- **Runtime**: Python 3.11, FastAPI
- **Database**: PostgreSQL 15 (primary), Redis 7 (cache + queues)
- **Message Queue**: RabbitMQ (notification async processing)
- **Container**: Docker + Kubernetes (EKS)
- **Infra**: AWS (ECS Fargate for services, RDS for Postgres, ElastiCache for Redis)
- **CI/CD**: GitHub Actions → ECR → EKS rolling deploy

## Running Locally

```bash
docker-compose up --build
```

Services start on ports 8001–8004. Postgres on 5432, Redis on 6379.

## Configuration

All config lives in `config/settings.py` and `config/services.env`.
Critical values: connection pool sizes, rate limits, timeouts, worker counts.
See `config/settings.py` for annotated defaults.

## Runbooks

| ID | Scenario |
|----|----------|
| RB-0001 | Payment gateway timeout / charge failure |
| RB-0002 | Order processing spike / queue backup |
| RB-0003 | Inventory oversell / reservation drift |
| RB-0004 | High error rate — general escalation |
| RB-0005 | Notification delivery failure |
| RB-0006 | Database connection pool exhaustion |

All runbooks are in `docs/runbooks/`.

## On-Call

Primary: #oncall-ecomcore (Slack)
Escalation: platform-eng@company.com
PagerDuty: ecomcore-production service
