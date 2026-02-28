<!-- iq:runbook_id=RB-0005 | title=Notification Delivery Failure | first_action_step=Check SendGrid and Twilio status pages, then verify RabbitMQ queue depth and celery-worker pod health -->

# Notification Delivery Failure

**Service**: notification-service, celery-workers  
**Severity**: MED  
**On-call**: #oncall-ecomcore  

## Symptoms

- Order confirmation emails not arriving
- `notification-service` stats showing `failed` count rising
- Celery worker logs showing `MaxRetriesExceededError`
- RabbitMQ `notification.email` or `notification.sms` queue growing unbounded
- SendGrid or Twilio dashboard showing delivery errors

## Immediate Actions

1. Check SendGrid status: https://status.sendgrid.com
2. Check Twilio status: https://status.twilio.com
3. Check celery-worker pod health: `kubectl get pods -l app=celery-workers -n ecomcore`
4. Check RabbitMQ queue depth for notification queues
5. If workers are healthy but third-party down: messages will retry automatically
6. If workers are crashing: check for recent config changes to `SENDGRID_API_KEY` or `TWILIO_AUTH_TOKEN`

## Non-Critical Note

Notification failures do NOT affect order revenue flow.
Orders still process successfully — customers just won't receive confirmations.
Treat as MED unless failure persists > 1 hour (customer experience degradation).

## Recovery

Once third-party service recovers, RabbitMQ messages will be re-processed automatically.
If queue is dead-lettered (> `NOTIFICATION_MAX_RETRIES` attempts):
1. Check dead letter queue: `notification.email.dlq`
2. If customer impact significant: manually requeue via RabbitMQ management UI
3. Or: use admin endpoint to resend specific notifications by order_id
