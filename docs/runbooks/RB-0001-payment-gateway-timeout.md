<!-- iq:runbook_id=RB-0001 | title=Payment Gateway Timeout / Charge Failure | first_action_step=Check payment-service logs for Stripe API errors and verify PAYMENT_CHARGE_TIMEOUT config value -->

# Payment Gateway Timeout / Charge Failure

**Service**: payment-service  
**Severity**: HIGH  
**On-call**: #oncall-ecomcore  

## Symptoms

- Orders stuck in `processing_payment` status
- `payment-service` logs showing `requests.Timeout` or `stripe.error.APIConnectionError`
- `PAYMENT_CHARGE_TIMEOUT` exceeded errors in logs
- Customer-facing: "Payment processing failed, please try again"
- Order failure rate > 2% sustained for > 3 minutes

## Immediate Actions

1. Check payment-service health: `GET http://payment-service.internal:8002/health`
2. Tail payment-service logs: `kubectl logs -f deploy/payment-service -n ecomcore | grep ERROR`
3. Check Stripe status page: https://status.stripe.com — confirm no active incidents
4. Verify `PAYMENT_CHARGE_TIMEOUT` config (current: 6.0s, Stripe P99: ~3s)
5. Check Redis connectivity — idempotency cache depends on Redis
6. Review rate limit counter — if 429s from Stripe, `PAYMENT_RATE_LIMIT` may be exceeded

## Escalation Path

- **< 5 min**: Investigate logs, check Stripe status
- **5-15 min**: Page payment team lead if Stripe healthy but failures persist
- **> 15 min**: Engage Stripe support + escalate to CTO on-call

## Root Causes (historical)

| Cause | Frequency | Resolution |
|-------|-----------|------------|
| Stripe API degradation | 40% | Wait + retry |
| Config change to PAYMENT_CHARGE_TIMEOUT | 25% | Revert config |
| Redis connection exhaustion (idempotency cache) | 20% | Restart Redis, check pool size |
| Network partition (EKS → Stripe) | 10% | Check VPC NAT Gateway |
| Code change in payment calculation logic | 5% | Revert commit, check divisors/multipliers |

## Recovery Verification

1. Order failure rate back below 0.5%
2. `payment-service` health check returning 200
3. Stripe dashboard showing normal transaction volume
4. No stuck orders in `processing_payment` status > 2 minutes

## Post-Incident

- File incident report within 24h
- Review `PAYMENT_CHARGE_TIMEOUT` setting against Stripe P99 latency
- Verify idempotency key TTL prevents duplicate charges during retries
