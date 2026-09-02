# Billing service double-charging — Redis rate limiter fail-open

## Date
2026-07-15

## Authors
On-call SRE, Billing Team

## Status
Final

## Summary
On July 15th, 2026, a Redis connection pool exhaustion caused the rate limiter in `api_gateway` to fail open and the idempotency cache in `billing_service` to become unreachable. Stripe's automatic webhook retry mechanism then processed the same charge event twice, resulting in 450 customers being double-charged. The incident lasted 38 minutes before the root cause was traced and Redis connections were restored.

## Impact
SEV1. 450 customers double-charged. Estimated financial exposure: $18,000 in erroneous charges. Stripe refund batch issued the following morning. Significant support ticket volume for 48 hours post-incident.

## Root Cause
Redis became unreachable due to connection pool exhaustion caused by a sudden traffic spike from a marketing campaign. Two components simultaneously lost their Redis dependency:

1. **Rate limiter (api_gateway):** Configured to `fail-open` — when Redis is unavailable, the rate limiter allows all traffic rather than blocking. This allowed Stripe's webhook retry mechanism to deliver the same webhook event multiple times without being rate-limited.

2. **Idempotency key store (billing_service):** The billing service stores processed webhook event IDs in Redis with a 24-hour TTL. When Redis was unreachable, the idempotency lookup returned a cache miss, causing `billing_service` to treat the retried webhook as a new, unprocessed event. The ON CONFLICT upsert that was supposed to be the secondary safeguard failed silently because the idempotency check short-circuits before the DB write.

The combination of these two failures meant the same Stripe `invoice.paid` webhook was processed twice, resulting in two charges against the same customer card.

## Trigger
A marketing email campaign drove a 6x traffic spike over 20 minutes. The Redis connection pool (max=50) was exhausted by legitimate read-heavy traffic before the billing webhook burst arrived.

## Resolution
1. Restarted Redis to clear stale connections and restore the pool.
2. Deployed a hotfix to `billing_service` to add a database-level idempotency check as a hard fallback when Redis is unavailable — the DB INSERT now uses `ON CONFLICT (stripe_event_id) DO NOTHING` unconditionally, not conditionally after a Redis lookup.
3. Changed the rate limiter fail-open policy to fail-closed for the `/webhooks/` endpoint specifically.

## Detection
Stripe dashboard showed duplicate `invoice.paid` events being accepted. On-call engineer noticed duplicate charges in the billing_service audit log 22 minutes into the incident. PagerDuty alert on Redis connection pool utilization had fired 8 minutes earlier but was not immediately connected to the billing symptom.

## Timeline
- 2026-07-15T14:10:00Z — Marketing email campaign delivers; traffic spike begins.
- 2026-07-15T14:22:00Z — Redis connection pool hits 100% utilization.
- 2026-07-15T14:23:00Z — Rate limiter enters fail-open mode; idempotency lookups begin returning misses.
- 2026-07-15T14:24:00Z — First duplicate webhook processed; first double-charge occurs.
- 2026-07-15T14:30:00Z — PagerDuty fires on Redis pool exhaustion. On-call paged.
- 2026-07-15T14:38:00Z — On-call correlates billing duplicates with Redis outage.
- 2026-07-15T14:45:00Z — Redis restarted; pool drains and reconnects.
- 2026-07-15T14:48:00Z — Duplicate webhook processing stops; no further double-charges.
- 2026-07-15T15:02:00Z — Database-level idempotency hotfix deployed.

## Action Items
- Add unconditional `ON CONFLICT (stripe_event_id) DO NOTHING` to all billing_service webhook handlers regardless of Redis state.
- Change rate limiter to fail-closed on `/webhooks/stripe/*` routes.
- Add Redis connection pool saturation to SEV2 auto-page thresholds (currently only alerts at 100%; lower to 80%).
- Write runbook for "Redis unavailable during webhook processing" scenario.

## Lessons Learned
Fail-open rate limiting is appropriate for user-facing endpoints where availability > correctness, but is never appropriate for financial transaction endpoints where correctness must take priority. The idempotency cache should be a performance optimization, not the primary correctness mechanism — the database must be the authoritative safeguard.
