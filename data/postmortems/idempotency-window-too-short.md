# Idempotency window too short

## Date
2026-10-10

## Authors
Incident Response Team

## Status
Final

## Summary
On October 10th, 2026, an extended delay in Stripe's webhook delivery system exposed a flaw in our idempotency implementation. The `billing_service` was configured to expire idempotency keys after 1 hour. When Stripe delivered legitimate webhook retries 2 hours late, our system treated them as new events, resulting in duplicate charges for several dozen customers.

## Impact
SEV2. 75 customers were charged twice for their monthly subscription renewals. This caused significant customer frustration and required manual intervention by the finance team to issue refunds and apologize.

## Root Cause
The `billing_service` uses `redis` to store idempotency keys to deduplicate incoming Stripe webhooks before writing to `postgres`. To save memory, these keys were configured with a TTL (Time To Live) of 1 hour. On this day, Stripe experienced internal delays and paused webhook delivery. When they resumed 2 hours later, they re-sent events that our `api_gateway` had partially acknowledged but failed to fully process. Because 2 hours had passed, the idempotency keys in `redis` had expired. The `billing_service` received the retried webhooks, found no existing key, and processed the charge a second time.

## Trigger
An external delay at Stripe caused webhooks to be retried outside of our expected 1-hour idempotency window.

## Resolution
Engineers ran a script to cross-reference Stripe transaction IDs in our `postgres` database, identifying the 75 duplicate records. Refunds were initiated via the Stripe API. A hotfix was deployed to increase the idempotency key TTL to 72 hours.

## Detection
Customer support received a flurry of tickets from users complaining about double charges on their credit card statements.

## Timeline
- 2026-10-10T08:00:00Z - Stripe begins experiencing webhook delivery delays.
- 2026-10-10T09:00:00Z - Idempotency keys for early, partially-processed events expire in `redis`.
- 2026-10-10T10:00:00Z - Stripe resumes delivery and retries old webhooks.
- 2026-10-10T10:05:00Z - `billing_service` processes retries as new events; duplicate charges occur.
- 2026-10-10T11:30:00Z - Customer support escalates double-charge complaints.
- 2026-10-10T13:00:00Z - TTL increased to 72 hours; reconciliation script run.

## Action Items
- Increase idempotency key TTL in `redis` to 72 hours to accommodate maximum external retry windows.
- Move primary idempotency storage from ephemeral `redis` to persistent `postgres` records.
- Implement an automated daily audit that flags duplicate external transaction IDs.

## Lessons Learned
Idempotency windows must always be larger than the maximum possible retry window of any upstream provider. Using short TTLs to save memory on critical financial deduplication is a false economy.
