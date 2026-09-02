# Read replica lag stale data

## Date
2026-10-22

## Authors
Incident Response Team

## Status
Final

## Summary
On October 22nd, 2026, a massive data import job caused severe replication lag between the primary `postgres` instance and its read replicas. Because the `user_service` was configured to read exclusively from the replicas for performance, users experienced a confusing "stale data" state where profile updates appeared to revert themselves immediately after being saved.

## Impact
SEV2. Over a 2-hour period, thousands of users were unable to reliably update their profiles or see newly created API keys. While no data was actually lost, the user experience was severely degraded, leading to a flood of support tickets claiming the system was broken.

## Root Cause
The `postgres` database utilizes asynchronous replication to two read replicas. The `user_service` directs all read queries to these replicas to reduce load on the primary node. A massive, unthrottled data import job was executed on the primary node by the analytics team. This generated a massive WAL (Write-Ahead Log) backlog, causing replication lag to spike from 100ms to over 45 seconds. When a user updated their profile (written to the primary) and then refreshed the page (read from the replica), the replica returned the old data because it hadn't yet processed the update.

## Trigger
An unthrottled historical data backfill script run by the analytics team against the production primary database.

## Resolution
The data import script was forcefully terminated. The replication lag slowly caught up over the next 15 minutes. Once the replicas were in sync, the stale data issue resolved itself.

## Detection
Customer support received numerous reports of "ghost updates" where users would change their name, see success, but then see their old name upon refreshing the page.

## Timeline
- 2026-10-22T09:00:00Z - Analytics team begins unthrottled data import.
- 2026-10-22T09:15:00Z - `postgres` replication lag exceeds 30 seconds.
- 2026-10-22T09:30:00Z - Users begin experiencing and reporting stale data on reads.
- 2026-10-22T10:30:00Z - Engineering correlates the issue with the high replication lag metric.
- 2026-10-22T10:45:00Z - Import script killed.
- 2026-10-22T11:00:00Z - Replication catches up; issue resolves.

## Action Items
- Implement "read-your-own-writes" consistency in the `user_service` by routing reads to the primary for a short window after a write.
- Throttle all administrative data imports and run them against dedicated offline instances when possible.
- Set up alerts for replication lag exceeding 5 seconds.

## Lessons Learned
Eventual consistency is a powerful scaling tool but provides a terrible user experience if the propagation delay becomes noticeable to a human. Applications must design around replication lag.
