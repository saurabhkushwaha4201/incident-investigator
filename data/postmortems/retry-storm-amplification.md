# Retry storm amplification

## Date
2026-09-08

## Authors
Incident Response Team

## Status
Final

## Summary
On September 8th, 2026, a brief network blip caused `postgres` to return 503 errors to the `billing_service`. The `billing_service` was configured with an aggressive retry policy, which amplified the initial failure by generating 10x the normal request volume. This retry storm overwhelmed the database and extended a 30-second network blip into a 45-minute outage.

## Impact
SEV1. The `billing_service` was completely down for 45 minutes, preventing any new transactions or subscription updates.

## Root Cause
The `billing_service` uses an internal library to communicate with `postgres`. This library was recently updated to automatically retry failed queries up to 10 times without exponential backoff or jitter. When a brief network switch failure caused a momentary disconnection, the initial requests failed. The `billing_service` immediately fired 10 retries for every failed request. This massive 10x amplification instantly saturated the `postgres` connection pool and CPU. Even after the network recovered, the database could not process the backlog of retries fast enough, causing further timeouts and triggering even more retries in a catastrophic positive feedback loop.

## Trigger
A 30-second top-of-rack switch reboot caused a temporary network partition between the application and database tiers.

## Resolution
The `billing_service` was scaled down to zero pods to completely halt the traffic and break the retry loop. Once the `postgres` load normalized, the service was slowly scaled back up with a hotfix deployed to disable the aggressive retry logic.

## Detection
Alerts fired for 100% CPU utilization on `postgres` and a massive spike in 503 errors from the `billing_service`.

## Timeline
- 2026-09-08T11:00:00Z - Top-of-rack switch reboots; temporary network blip.
- 2026-09-08T11:00:30Z - `billing_service` initiates 10x retries, overwhelming `postgres`.
- 2026-09-08T11:02:00Z - `postgres` becomes unresponsive due to load.
- 2026-09-08T11:15:00Z - Engineering team identifies retry storm amplification.
- 2026-09-08T11:35:00Z - `billing_service` scaled to zero to kill the loop.
- 2026-09-08T11:45:00Z - Service restored with patched retry policy.

## Action Items
- Audit all internal services for unbounded retry loops.
- Enforce exponential backoff and randomized jitter for all database and service-to-service retries.
- Implement global circuit breaking in `billing_service` to fail fast when `postgres` is overloaded.

## Lessons Learned
Naive retry logic without backoff is a fast track to self-denial-of-service. Systems must be designed to shed load during failure, not multiply it.
