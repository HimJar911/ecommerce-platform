<!-- iq:runbook_id=RB-0002 | title=Order Processing Spike / Queue Backup | first_action_step=Check order-service pod count and HPA status, then verify RabbitMQ queue depth -->

# Order Processing Spike / Queue Backup

**Service**: order-service, notification-service  
**Severity**: HIGH  
**On-call**: #oncall-ecomcore  

## Symptoms

- Orders per minute > 120 (normal peak: 85/min)
- order-service CPU > 80% across all pods
- RabbitMQ `order.events` queue depth > 5000 messages
- P99 order latency > 8s SLA
- HPA at max replicas (20) and still lagging

## Immediate Actions

1. Check HPA status: `kubectl get hpa order-service-hpa -n ecomcore`
2. Manually scale if HPA isn't reacting fast enough:
   `kubectl scale deploy/order-service --replicas=20 -n ecomcore`
3. Check RabbitMQ queue depth: `http://rabbitmq.internal:15672/#/queues`
4. Check if spike is organic (flash sale?) or anomalous (DDoS/scraper)
5. If anomalous: enable rate limiting at nginx layer
6. Check `WEB_CONCURRENCY` — may need temporary increase if CPU slack available

## Flash Sale Mode

If spike is from a scheduled flash sale (check marketing calendar):

1. Pre-scale order-service to 18 replicas 30 min before sale
2. Pre-scale celery-workers to 10 replicas
3. Set `ORDER_MAX_RETRIES=1` to reduce queue amplification during peak
4. Monitor every 5 minutes during active sale

## Queue Recovery

If queue backed up:
1. Add more celery-workers: `kubectl scale deploy/celery-workers --replicas=10`
2. Monitor drain rate in RabbitMQ management UI
3. Normal drain time: ~20 min per 10k messages with 8 workers

## Escalation

- **> 10 min at peak**: Engage infrastructure team for emergency scaling
- **Order failure rate > 5%**: Escalate to engineering lead
- **Queue > 50k messages**: Consider enabling order queuing mode (requires feature flag)

## Root Causes (historical)

| Cause | Frequency |
|-------|-----------|
| Scheduled flash sale (under-provisioned) | 55% |
| Bot traffic / scraper spike | 20% |
| Downstream service slowdown (payment/inventory) creating backpressure | 15% |
| Deploy during peak traffic | 10% |
