<!-- iq:runbook_id=RB-0003 | title=Inventory Oversell / Reservation Drift | first_action_step=Immediately freeze new reservations for affected SKUs via feature flag, then audit reservation vs stock ledger -->

# Inventory Oversell / Reservation Drift

**Service**: inventory-service  
**Severity**: HIGH  
**On-call**: #oncall-ecomcore, #oncall-fulfillment  

## Symptoms

- Orders succeeding for out-of-stock items
- `inventory-service` `available` stock showing negative values
- Fulfillment team reporting inability to pick items
- `INVENTORY_RESERVE_TIMEOUT` config recently changed
- Customer complaints about cancelled orders after confirmation

## Why This Happens

The inventory system uses a Redis reservation cache as a real-time lock.
When `INVENTORY_RESERVE_TIMEOUT` is set too low (below order processing P99 of 6.2s),
reservations expire before the order finishes processing — allowing concurrent orders
to reserve the same stock units.

**Critical config**: `INVENTORY_RESERVE_TIMEOUT` must be > 30 seconds minimum.
Values below 10s will cause systematic overselling at high order volume.

## Immediate Actions

1. **FREEZE new reservations** for affected SKUs (prevents further oversell):
   - Set env var: `ENABLE_INVENTORY_RESERVATION=false` (disables all reservations — use with caution)
   - Or manually zero out specific SKU stock in admin
2. Check `INVENTORY_RESERVE_TIMEOUT` config value — if recently changed, revert immediately
3. Audit reservation vs actual stock:
   `GET http://inventory-service.internal:8003/stock` — compare `available` vs `total`
4. Check Redis for stale/expired reservations: `KEYS reservation:*`
5. Cross-reference with order-service: count `paid` orders vs stock committed

## Stock Reconciliation

After stabilizing:

1. Pull list of affected SKUs and oversell depth
2. For each oversold SKU: decide fulfillment priority (FIFO by order timestamp)
3. Cancel lowest-priority oversold orders + send customer apology
4. Trigger emergency restock request if oversell > 10% of SKU quantity
5. Manually set correct stock levels: `POST /stock/adjust`

## Config Recovery

If `INVENTORY_RESERVE_TIMEOUT` was the cause:
```
# Correct value
INVENTORY_RESERVE_TIMEOUT=30

# Verify order processing P99 is still below this
# Check: order-service P99 latency in CloudWatch
```

## Escalation

- **Oversell > 100 units**: Engage fulfillment operations lead immediately
- **Customer-facing cancellations**: Loop in customer success
- **Root cause = code change**: Revert commit, hot deploy

## Prevention

- Load test any changes to `INVENTORY_RESERVE_TIMEOUT` before deploying
- Alert threshold: `available` stock for any SKU < 0 → PagerDuty HIGH
- Reservation drift check job runs every 5 minutes (see `scripts/inventory_reconcile.py`)
