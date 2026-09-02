# Idempotent upsert race billing undercount

## Date
2026-08-12

## Authors
Incident Response Team

## Status
Final

## Summary
On August 12th, 2026, a race condition in the `billing_service` allowed concurrent API usage reports to bypass the `postgres` idempotency constraint. This resulted in double-metering of the same event, which paradoxically led to an undercount in the final billing aggregation due to a subsequent data deduplication pipeline misinterpreting the state.

## Impact
SEV2. Approximately 5% of API usage events for high-volume customers were undercounted over a 24-hour period. This resulted in an estimated $4,000 loss in unbilled usage.

## Root Cause
The `billing_service` records API usage events in `postgres`. It uses an idempotency key provided by the client. The application logic first checked if the key existed; if not, it inserted the record. However, it did not rely on a unique constraint at the `postgres` schema level for the idempotency key. Concurrent requests hit the `billing_service` before the first request could write the key, bypassing the application-level check. Both records were inserted. Later, the billing aggregation job saw duplicate keys and, assuming data corruption, dropped both records instead of keeping one.

## Trigger
A major customer migrated their infrastructure, causing a sudden influx of heavily parallelized, retried API requests.

## Resolution
A schema migration was immediately applied to `postgres` to add a `UNIQUE` constraint on the idempotency key column. The `billing_service` code was updated to rely on the `ON CONFLICT DO NOTHING` database feature rather than a read-then-write application-level check.

## Detection
The finance team noticed a discrepancy between `api_gateway` request logs and the billed usage for a specific enterprise customer during a routine daily audit.

## Timeline
- 2026-08-12T02:00:00Z - Customer begins parallel migration script.
- 2026-08-12T02:15:00Z - Race conditions in `billing_service` lead to duplicate idempotency keys in `postgres`.
- 2026-08-12T04:00:00Z - Billing aggregation job runs and drops duplicates, causing undercount.
- 2026-08-13T09:00:00Z - Finance identifies discrepancy.
- 2026-08-13T11:00:00Z - Engineering identifies the read-then-write anti-pattern.
- 2026-08-13T13:00:00Z - Schema constraint added; code deployed.

## Action Items
- Enforce unique constraints at the `postgres` schema level for all idempotency keys.
- Refactor all read-then-write patterns in `billing_service` to use `ON CONFLICT`.
- Fix the billing aggregation job to keep the first record rather than dropping all duplicates.

## Lessons Learned
Application-level idempotency checks are insufficient under high concurrency. The database must be the final arbiter of uniqueness. Data pipelines must be robust against duplicates and not overreact by dropping valid data.
