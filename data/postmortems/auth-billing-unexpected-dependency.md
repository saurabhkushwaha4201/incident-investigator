# Auth billing unexpected dependency

## Date
2026-10-18

## Authors
Incident Response Team

## Status
Final

## Summary
On October 18th, 2026, an unannounced architectural change coupled the `auth_service` directly to the `billing_service`. When the `billing_service` experienced a brief database outage, this unexpected dependency caused the `auth_service` to also fail, turning a localized billing issue into a global authentication outage that locked all users out of the platform.

## Impact
SEV1. 100% of logins and API requests requiring token validation failed for 45 minutes. The blast radius was significantly larger than the initial failure.

## Root Cause
Historically, the `auth_service` operated independently to validate JWTs. A recent feature required checking a user's subscription status during login to populate a custom claim in the JWT. To implement this, engineers added a synchronous HTTP call from the `auth_service` to the `billing_service` on the critical login path. When a bad migration locked a table in `postgres`, the `billing_service` became unresponsive. Because the `auth_service` was now synchronously dependent on `billing_service`, all login attempts timed out. The architectural failure was tight coupling without circuit breakers or fallback mechanisms.

## Trigger
A bad database migration locked a core table in `postgres`, taking down the `billing_service`.

## Resolution
The database migration was aborted, freeing the locks and restoring the `billing_service`, which automatically restored the `auth_service`. A hotfix was subsequently deployed to the `auth_service` to wrap the billing call in a circuit breaker and fail open (issue the JWT without the custom claim) if the `billing_service` is unavailable.

## Detection
Alerts fired for 500 errors in the `billing_service`, followed almost immediately by a complete collapse of successful logins in the `auth_service`.

## Timeline
- 2026-10-18T14:00:00Z - Bad migration applied to `postgres`.
- 2026-10-18T14:01:00Z - `billing_service` becomes unresponsive.
- 2026-10-18T14:02:00Z - `auth_service` begins timing out on all login requests.
- 2026-10-18T14:15:00Z - Engineering team discovers the undocumented synchronous dependency.
- 2026-10-18T14:45:00Z - Migration aborted; both services recover.

## Action Items
- Remove synchronous cross-service dependencies on the critical authentication path.
- Implement circuit breakers and graceful degradation for all inter-service HTTP calls.
- Enforce strict architectural reviews for new feature dependencies.

## Lessons Learned
Coupling core infrastructure (auth) to higher-level business logic (billing) expands the blast radius of failures unacceptably. The critical path must remain isolated.
