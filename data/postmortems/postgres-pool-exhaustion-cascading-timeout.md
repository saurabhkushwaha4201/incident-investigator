# Postgres pool exhaustion cascading timeout

## Date
2026-08-25

## Authors
Incident Response Team

## Status
Final

## Summary
On August 25th, 2026, a sudden spike in billing reconciliation jobs overwhelmed the `postgres` connection pool. With all connections in use, incoming requests to both `billing_service` and `user_service` queued up, eventually timing out. This created a cascading failure that brought down most of the backend API for 25 minutes.

## Impact
SEV2. 100% of API requests requiring database access failed with 504 Gateway Timeout or 500 Internal Server Error for 25 minutes. Customer dashboards failed to load, and new account creations were halted.

## Root Cause
The `billing_service` and `user_service` share a single `postgres` database cluster. During an end-of-month reconciliation spike, the `billing_service` spawned hundreds of worker threads, instantly exhausting the `postgres` connection pool (max 10/10 active connections per pgbouncer node). As `user_service` and `billing_service` web requests attempted to checkout connections, they blocked. Thread pools in the services filled up, and the `api_gateway` eventually timed out waiting for responses, causing a cascading failure.

## Trigger
A cron job for end-of-month billing reconciliation was accidentally configured to run synchronously with high concurrency instead of using the async job queue.

## Resolution
The rogue cron job was manually killed. The `postgres` connection pool was flushed, and `billing_service` pods were restarted to clear stuck threads. Services recovered immediately once connections were freed.

## Detection
Alerts for high response latency at the `api_gateway` fired, quickly followed by `postgres` connection pool exhaustion alerts.

## Timeline
- 2026-08-25T08:00:00Z - End-of-month reconciliation cron job starts.
- 2026-08-25T08:02:00Z - `postgres` connection pool reaches 100% utilization.
- 2026-08-25T08:05:00Z - `user_service` and `billing_service` start queuing requests; thread pools fill.
- 2026-08-25T08:10:00Z - `api_gateway` starts returning 504 Timeouts. Alerts trigger.
- 2026-08-25T08:20:00Z - Engineering identifies rogue cron job and terminates it.
- 2026-08-25T08:25:00Z - Services restarted; traffic flow returns to normal.

## Action Items
- Separate `postgres` connection pools for web traffic and background jobs.
- Refactor the reconciliation cron job to use the async job queue.
- Implement strict timeouts on database connection checkout in `user_service` and `billing_service`.

## Lessons Learned
Shared connection pools are a major vector for cascading failures. Background jobs must be isolated from synchronous web traffic resources.
