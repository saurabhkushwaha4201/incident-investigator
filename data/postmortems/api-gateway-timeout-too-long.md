# API Gateway timeout too long

## Date
2026-09-12

## Authors
Incident Response Team

## Status
Final

## Summary
On September 12th, 2026, the `api_gateway` experienced a catastrophic thread pool exhaustion caused by an overly permissive timeout configuration. When the downstream `billing_service` experienced minor latency degradation, the `api_gateway` held connections open for up to 60 seconds, quickly depleting available threads and causing a platform-wide outage.

## Impact
SEV1. 100% of all API requests failed for 35 minutes. No users could log in, access data, or process payments.

## Root Cause
The `api_gateway` forwards requests to various backend services. Its global timeout was set to a legacy value of 60 seconds to accommodate slow legacy reports. The `billing_service` experienced a temporary slowdown due to a heavy `postgres` query, causing response times to increase from 200ms to 45 seconds. Because the `api_gateway` timeout was 60s, it kept the client connections open while waiting for the `billing_service`. The thread pool in the `api_gateway` (capacity: 2000) filled up in less than 3 minutes. Once the pool was exhausted, the `api_gateway` stopped accepting new connections entirely, turning a localized slowdown into a global outage.

## Trigger
A long-running, unoptimized analytical query in the `billing_service` degraded its response time.

## Resolution
The `api_gateway` was restarted to clear the hung connections. A hotfix was deployed to reduce the global timeout to 5 seconds, shedding load and allowing the gateway to recover even while the `billing_service` was still degraded.

## Detection
Monitoring detected a complete drop in successful API requests across all endpoints, followed immediately by thread exhaustion alerts on the `api_gateway`.

## Timeline
- 2026-09-12T14:00:00Z - Slow query executes in `billing_service`.
- 2026-09-12T14:02:00Z - `billing_service` response times climb above 40s.
- 2026-09-12T14:05:00Z - `api_gateway` thread pool exhausted; global outage begins.
- 2026-09-12T14:15:00Z - Engineering links the outage to the 60s timeout configuration.
- 2026-09-12T14:35:00Z - `api_gateway` timeout reduced and pods restarted; service restored.

## Action Items
- Enforce strict, short timeouts (e.g., 5 seconds maximum) on all `api_gateway` routes.
- Implement per-route timeout overrides for specific, known-slow endpoints rather than a permissive global default.
- Optimize the slow query in the `billing_service`.

## Lessons Learned
Generous timeouts mask downstream latency issues until they manifest as catastrophic upstream thread exhaustion. Fail fast is essential for system stability.
