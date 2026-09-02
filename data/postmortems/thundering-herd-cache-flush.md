# Thundering herd cache flush

## Date
2026-09-02

## Authors
Incident Response Team

## Status
Final

## Summary
On September 2nd, 2026, a misconfigured deployment script executed a global flush of the `redis` cache. This caused an immediate cache stampede (thundering herd) as all incoming API requests missed the cache simultaneously, resulting in a massive overload of the `postgres` database and bringing down the platform for 18 minutes.

## Impact
SEV1. 100% of API traffic was disrupted for 18 minutes, resulting in widespread 503s across all services. 

## Root Cause
The `user_service` relies heavily on `redis` to cache user profiles and account metadata to reduce load on `postgres`. During a deployment of a minor configuration change, the CI/CD pipeline executed a `FLUSHALL` command on the `redis` cluster, intending to clear a specific staging environment, but it was mistakenly run against production. Upon the flush, thousands of concurrent requests to the `api_gateway` routed to the `user_service` experienced cache misses. The `user_service` subsequently forwarded all these requests to `postgres`. The database CPU spiked to 100%, and connection pools were exhausted, leading to system-wide timeouts.

## Trigger
A CI/CD pipeline misconfiguration executed a global cache flush on the production `redis` cluster during a routine deployment.

## Resolution
Traffic was temporarily diverted at the `api_gateway` to allow `postgres` to recover. We then slowly ramped up traffic over 10 minutes to allow the `redis` cache to warm up gradually without overwhelming the database. 

## Detection
Alerts for `redis` cache hit rate dropping to 0% fired immediately, followed by severe latency alerts from `postgres` and the `api_gateway`.

## Timeline
- 2026-09-02T14:00:00Z - CI/CD pipeline executes `FLUSHALL` on production `redis`.
- 2026-09-02T14:01:00Z - Cache hit rate drops to 0; cache stampede begins.
- 2026-09-02T14:02:00Z - `postgres` CPU hits 100%, connection pools exhaust.
- 2026-09-02T14:05:00Z - Engineering identifies the cache flush.
- 2026-09-02T14:10:00Z - `api_gateway` traffic throttled to 10% to allow database recovery.
- 2026-09-02T14:18:00Z - Cache sufficiently warmed; traffic restored to 100%.

## Action Items
- Restrict `FLUSHALL` command access in production `redis` clusters.
- Implement a cache warming script for deployment procedures.
- Add random jitter to cache TTLs in `user_service` to prevent simultaneous expiration.

## Lessons Learned
Cold cache scenarios must be explicitly tested. A database sized for a 95% cache hit rate will instantly collapse if the cache is bypassed.
