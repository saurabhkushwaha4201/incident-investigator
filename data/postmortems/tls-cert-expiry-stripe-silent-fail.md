# TLS cert expiry Stripe silent fail

## Date
2026-09-15

## Authors
Incident Response Team

## Status
Final

## Summary
On September 15th, 2026, an expired TLS client certificate used by the `billing_service` to authenticate with a legacy Stripe proxy endpoint caused all outbound payment calls to fail. Due to improper error handling, the failures were logged as generic network errors, and the system failed silently, leaving users thinking their payments succeeded.

## Impact
SEV2. Over a 4-hour window, 350 subscription upgrades were processed by the frontend but never successfully communicated to Stripe. This resulted in users receiving premium access without being charged.

## Root Cause
The `billing_service` communicates with a specific Stripe endpoint that requires Mutual TLS (mTLS). The client certificate loaded into the `billing_service` expired. When the service attempted to establish a connection, the handshake failed. However, the exception handling block in the `billing_service` caught the TLS handshake error and inadvertently swallowed it, returning a HTTP 200 OK to the calling `api_gateway`. As a result, the system acted as if the transaction succeeded, granting user access without executing the financial charge.

## Trigger
The natural expiration of a 1-year mTLS client certificate that was not tracked in our centralized certificate management system.

## Resolution
The certificate was manually renewed and injected into the `billing_service` pods. A script was run to identify the 350 users who received unbilled upgrades and retroactively bill them via Stripe.

## Detection
The finance team noticed a sharp discrepancy between daily active premium users and the day's total Stripe revenue.

## Timeline
- 2026-09-15T00:00:00Z - Client TLS certificate expires.
- 2026-09-15T00:15:00Z - First outbound Stripe call fails silently.
- 2026-09-15T04:00:00Z - Finance team detects revenue anomaly.
- 2026-09-15T04:30:00Z - Engineering discovers the swallowed TLS exception in logs.
- 2026-09-15T05:00:00Z - Certificate renewed; `billing_service` restarted.
- 2026-09-15T06:00:00Z - Missed charges retroactively applied.

## Action Items
- Add all client certificates to the centralized tracking and alerting system.
- Fix the exception handling in `billing_service` to propagate critical integration errors rather than returning 200 OK.
- Implement synthetic monitoring that actually verifies end-to-end payment flow.

## Lessons Learned
Swallowing errors to maintain a facade of stability leads to data corruption and financial loss. Always surface integration failures explicitly.
