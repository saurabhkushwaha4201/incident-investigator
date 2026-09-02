# Webhook HMAC mismatch silent drop

## Date
2026-08-01

## Authors
Incident Response Team

## Status
Final

## Summary
On August 1st, 2026, the HMAC secret used to verify Stripe webhooks was rotated in the parameter store. However, the `billing_service` was not redeployed to pick up the new secret. For 47 minutes, all incoming Stripe webhooks had signature mismatches and were silently dropped by the `billing_service`, leading to unrecorded payments and missing subscription updates.

## Impact
SEV2. Payments processed by Stripe during this 47-minute window were not reflected in our `postgres` database. No users were incorrectly charged, but 120 user accounts were not provisioned with their new subscription tiers, leading to customer confusion and support tickets.

## Root Cause
The `billing_service` caches the Stripe HMAC secret in memory on startup. When the secret was rotated in the centralized parameter store, the `billing_service` pods were not restarted. As a result, the `billing_service` continued verifying incoming webhook signatures against the old secret. The signature verification failed, and the service was configured to return a 200 OK (to prevent retries of invalid requests) while silently dropping the event.

## Trigger
A routine rotation of the Stripe HMAC secret by the security team.

## Resolution
The `billing_service` was manually redeployed, causing it to load the new HMAC secret. We then used the Stripe dashboard to replay all webhooks that failed during the 47-minute window.

## Detection
A customer support agent escalated a ticket where a user provided a valid Stripe receipt, but their account was still on the free tier. Investigation revealed missing webhooks in the logs.

## Timeline
- 2026-08-01T10:00:00Z - Security team rotates Stripe HMAC secret.
- 2026-08-01T10:05:00Z - First webhook fails HMAC validation and is silently dropped by `billing_service`.
- 2026-08-01T10:35:00Z - Customer support receives first ticket about missing subscription.
- 2026-08-01T10:45:00Z - Engineering identifies HMAC mismatch in logs.
- 2026-08-01T10:47:00Z - `billing_service` redeployed; webhook processing resumes.
- 2026-08-01T11:15:00Z - Missed webhooks replayed from Stripe.

## Action Items
- Modify `billing_service` to emit a high-priority metric/alert when HMAC validation fails.
- Implement an automated redeploy trigger when parameter store secrets are updated.
- Update runbooks to include secret rotation procedures.

## Lessons Learned
Silently dropping webhooks for security reasons (to prevent timing attacks or retry storms) is standard, but failing to alert internally on a sudden spike in these drops blinds us to configuration errors.
