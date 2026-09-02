# Clock skew JWT 401

## Date
2026-10-02

## Authors
Incident Response Team

## Status
Final

## Summary
On October 2nd, 2026, a failure in the NTP (Network Time Protocol) synchronization on two specific nodes hosting the `auth_service` caused their system clocks to drift significantly into the future. This caused them to incorrectly evaluate the `exp` (expiration) claim of newly issued JWTs, resulting in intermittent 401 Unauthorized errors for a subset of users.

## Impact
SEV2. Approximately 10% of all authenticated API requests were rejected with 401 errors over a 3-hour period. Users experienced a frustrating, flaky application state where reloading the page would often fix the issue temporarily.

## Root Cause
The `api_gateway` issues JWTs upon login, and the `auth_service` validates them on subsequent requests. Two virtual machines in the `auth_service` node pool lost contact with their NTP server due to a localized routing issue. Over the course of a week, their internal clocks drifted approximately 5 minutes into the future. When a user presented a newly minted JWT (valid for 15 minutes), these two drifted nodes evaluated the `exp` timestamp against their future-skewed clocks. For tokens close to their expiration, or when the drift worsened, the nodes mistakenly concluded the tokens had already expired, rejecting the requests.

## Trigger
A silent failure of the `chronyd` daemon on two specific Kubernetes worker nodes.

## Resolution
The two affected nodes were cordoned and drained. The `auth_service` pods were rescheduled onto healthy nodes with accurate clocks, instantly resolving the validation errors.

## Detection
Customer support reported intermittent login failures. Engineering noticed that the 401 error rate was perfectly correlated with traffic hitting two specific IP addresses in the `auth_service` cluster.

## Timeline
- 2026-09-25T00:00:00Z - NTP daemon fails silently on two nodes. Clock begins drifting.
- 2026-10-02T08:00:00Z - Drift exceeds 5 minutes; JWT validation begins failing.
- 2026-10-02T09:30:00Z - Support receives multiple reports of "flaky" logins.
- 2026-10-02T10:45:00Z - Engineering isolates the issue to two specific nodes.
- 2026-10-02T11:00:00Z - Nodes drained; traffic routed to healthy nodes.

## Action Items
- Implement explicit monitoring and alerting for NTP sync status and clock drift across all nodes.
- Add a small grace period (e.g., `leeway` of 60 seconds) to JWT expiration validation in the `auth_service` to tolerate minor clock skew.
- Ensure node provisioning scripts include aggressive health checks for time synchronization.

## Lessons Learned
Distributed authentication relies entirely on shared assumptions about time. Even minor clock drift can completely break cryptographically secure token validation in unpredictable ways.
