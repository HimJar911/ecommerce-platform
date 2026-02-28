<!-- iq:runbook_id=RB-0006 | title=Database Connection Pool Exhaustion | first_action_step=Check DB_POOL_SIZE config across all services — total connections must not exceed RDS max_connections of 200 -->

# Database Connection Pool Exhaustion

**Service**: order-service, payment-service, inventory-service  
**Severity**: HIGH  
**On-call**: #oncall-ecomcore, #oncall-infrastructure  

## Symptoms

- `sqlalchemy.exc.TimeoutError: QueuePool limit of size X overflow Y reached`
- Services returning 500 errors for any DB-dependent operation
- RDS CloudWatch metric `DatabaseConnections` approaching 200
- `DB_POOL_TIMEOUT` exceeded errors in logs
- All services degrading simultaneously (sign of DB-level issue, not service-level)

## Connection Math

```
Services using DB: order-service (3 replicas), payment-service (2), inventory-service (2) = 7 pods
Workers per pod: WEB_CONCURRENCY=9
Pool per worker: DB_POOL_SIZE=20
Max overflow per worker: DB_MAX_OVERFLOW=5

Max possible connections: 7 pods × 9 workers × (20 + 5) = 1,575
RDS max_connections (db.r5.large): 200

CRITICAL: Pool must be sized to: max_connections / (pods × workers)
Current safe value: 200 / (7 × 9) ≈ 3.17 → set DB_POOL_SIZE=3, DB_MAX_OVERFLOW=1
```

If DB_POOL_SIZE was recently increased without adjusting for pod count, pool exhaustion is the cause.

## Immediate Actions

1. Check RDS `DatabaseConnections` metric — if near 200, connection exhaustion confirmed
2. Check `DB_POOL_SIZE` across all service configs — was it recently changed?
3. If exhausted: restart affected services to clear connections
   `kubectl rollout restart deploy/order-service deploy/payment-service -n ecomcore`
4. If restart doesn't help: check for connection leaks (transactions not closed)
5. Emergency: reduce WEB_CONCURRENCY to 3 on all services to free connections

## Config Fix

```python
# Correct pool sizing for current pod count
# (update if pod count changes)
DB_POOL_SIZE = 3       # was incorrectly set to 20
DB_MAX_OVERFLOW = 1
DB_POOL_TIMEOUT = 10   # fail fast rather than hang
```

## Prevention

- Pool size alert: if `DatabaseConnections > 160` → PagerDuty warning
- Any change to `DB_POOL_SIZE` or `WEB_CONCURRENCY` requires pool math review
- Add to deploy checklist: verify connection math before scaling pod count
