# Redis partial outage circuit breaker flapping

## Date
2026-08-18

## Authors
Incident Response Team

## Status
Final

## Summary
On August 18th, 2026, two out of three nodes in the `redis` cluster became unresponsive. This caused the rate limiter circuit breaker in the `api_gateway` to rapidly oscillate (flap) between open and closed states. The resulting instability caused intermittent 503 Service Unavailable errors for users over a 40-minute period.

## Impact
SEV2. Approximately 15% of all API requests handled by the `api_gateway` returned 503 errors. The intermittent nature of the errors caused automated client scripts to fail unpredictably.

## Root Cause
The `api_gateway` uses a circuit breaker to protect the `redis` rate limiter. When 2 of the 3 `redis` nodes went down, the error rate spiked, opening the circuit breaker. However, the circuit breaker was configured with a very short half-open probe time (1 second) and a low success threshold. The single surviving `redis` node successfully handled the probe requests, causing the circuit breaker to close. Immediately, the flood of traffic overwhelmed the single node, causing it to time out, which reopened the circuit breaker. This flap cycle repeated every few seconds.

## Trigger
A routine hardware maintenance event by the cloud provider caused two `redis` nodes to reboot simultaneously.

## Resolution
Engineers manually forced the circuit breaker in the `api_gateway` to remain open (failing open to allow all traffic without rate limiting) until the `redis` cluster fully recovered. The `redis` nodes came back online after 30 minutes, and the circuit breaker was returned to automatic mode.

## Detection
Multiple automated alerts triggered for elevated 503 error rates from the `api_gateway`.

## Timeline
- 2026-08-18T18:00:00Z - Cloud provider reboots two `redis` nodes.
- 2026-08-18T18:01:00Z - Circuit breaker in `api_gateway` begins flapping.
- 2026-08-18T18:05:00Z - PagerDuty alerts engineering to 503 spike.
- 2026-08-18T18:15:00Z - Root cause identified as circuit breaker oscillation.
- 2026-08-18T18:20:00Z - Circuit breaker manually pinned open. 503s cease.
- 2026-08-18T18:40:00Z - `redis` cluster fully recovers. Circuit breaker reset.

## Action Items
- Tune circuit breaker parameters in `api_gateway`: increase half-open probe time and require a higher success count before fully closing.
- Implement adaptive rate limiting that degrades gracefully when `redis` capacity is diminished.
- Improve `redis` cluster resilience to handle simultaneous node failures more gracefully.

## Lessons Learned
Poorly tuned circuit breakers can cause more harm than good by creating systemic instability. Circuit breakers need exponential backoff on their probe attempts to prevent flapping.
