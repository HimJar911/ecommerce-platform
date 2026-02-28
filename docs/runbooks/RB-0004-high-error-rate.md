<!-- iq:runbook_id=RB-0004 | title=High Error Rate — General Escalation | first_action_step=Page on-call lead via PagerDuty, enable enhanced logging on all affected services, and check for recent deployments in the last 2 hours -->

# High Error Rate — General Escalation

**Service**: any  
**Severity**: HIGH  
**On-call**: #oncall-ecomcore  

## Trigger

5xx error rate > 2% sustained for 3+ minutes on any core service.

## Immediate Actions

1. Page on-call lead: PagerDuty → ecomcore-production
2. Join #incident-ecomcore Slack channel
3. Check recent deploys: `kubectl rollout history deploy -n ecomcore`
4. Enable enhanced logging: set `LOG_LEVEL=DEBUG` on affected service
5. Check all service health endpoints:
   - `GET /health` on order-service (8001), payment-service (8002), inventory-service (8003), notification-service (8004)
6. Review last 30 minutes of CloudWatch logs for ERROR patterns
7. Check if error is isolated to one service or cascading

## Blast Radius Assessment

Map the error to service call chain:
```
client → order-service → payment-service
                       → inventory-service
                       → notification-service (async, non-critical)
```

If order-service is erroring: all downstream services suspect.
If payment-service only: order creation blocked but inventory/notification unaffected.

## Rollback Procedure

If recent deploy is suspected:
```bash
# Rollback to previous deployment
kubectl rollout undo deploy/<service-name> -n ecomcore

# Verify rollback
kubectl rollout status deploy/<service-name> -n ecomcore
```

## Escalation Timeline

- **0-5 min**: Investigate, check health, look for obvious cause
- **5-15 min**: If not resolved, page engineering lead
- **15-30 min**: If revenue impact confirmed, page CTO on-call
- **> 30 min**: Full incident bridge + customer communication
