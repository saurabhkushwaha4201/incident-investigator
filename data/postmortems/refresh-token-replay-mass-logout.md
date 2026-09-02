# Refresh token replay mass logout

## Date
2026-08-10

## Authors
Incident Response Team

## Status
Final

## Summary
On August 10th, 2026, a bug in the `auth_service`'s refresh token rotation logic allowed old refresh tokens to be reused if presented concurrently. This triggered a security mechanism designed to invalidate all sessions for a user if a compromised token is detected. The resulting session invalidation storm logged out approximately 12,000 legitimate users.

## Impact
SEV1. 12,000 active users were unexpectedly logged out and forced to re-authenticate. This caused a massive spike in login requests, temporarily degrading `api_gateway` performance and overwhelming support channels.

## Root Cause
The `auth_service` rotates refresh tokens on use. To prevent token theft, if an old refresh token is used, the system assumes theft and revokes all active sessions for that user by clearing their entries in `redis`. A bug in the token rotation endpoint lacked proper concurrency locks. When a client application fired two identical refresh requests simultaneously, both succeeded, but the second request was treated as a replay of an old token, triggering the mass invalidation protocol.

## Trigger
A new version of our frontend SPA was deployed that had a bug causing it to aggressively retry refresh token requests if the network was slow.

## Resolution
The frontend SPA was rolled back to prevent the concurrent retry behavior. A hotfix was applied to `auth_service` to add a short grace period (5 seconds) during which a just-rotated refresh token could still be accepted without triggering mass invalidation.

## Detection
Alerts triggered for a massive spike in `redis` DEL commands and a corresponding drop in active user sessions.

## Timeline
- 2026-08-10T15:00:00Z - Frontend SPA version 2.4 deployed.
- 2026-08-10T15:05:00Z - Slow network clients begin firing concurrent refresh requests.
- 2026-08-10T15:10:00Z - `auth_service` begins interpreting concurrent requests as token theft.
- 2026-08-10T15:15:00Z - 12,000 users logged out; `redis` session deletion storm begins.
- 2026-08-10T15:20:00Z - Engineering identifies SPA retry bug.
- 2026-08-10T15:25:00Z - SPA rolled back to 2.3; logout storm subsides.

## Action Items
- Add distributed locks in `auth_service` when processing refresh tokens.
- Implement a 5-second grace period for recently rotated refresh tokens to handle network retries gracefully.
- Add rate limiting specifically for the token refresh endpoint in `api_gateway`.

## Lessons Learned
Security mechanisms designed to protect users (like mass session invalidation) can become weapons of self-destruction if triggered by benign client bugs. Concurrency must be strictly managed in token rotation endpoints.
