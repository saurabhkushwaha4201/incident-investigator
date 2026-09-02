# Config flag partial deploy

## Date
2026-09-24

## Authors
Incident Response Team

## Status
Final

## Summary
On September 24th, 2026, a feature flag intended to alter the tax calculation logic in the `billing_service` was partially deployed. Only 3 out of the 5 pods were successfully restarted to pick up the new flag, resulting in highly inconsistent billing behavior where users were charged different amounts for the exact same transaction depending on which pod handled their request.

## Impact
SEV2. Over 6 hours, approximately 1,200 invoices were generated with incorrect tax amounts. This required a massive manual reconciliation effort by the finance team and issuing corrected invoices to angry customers.

## Root Cause
The `billing_service` reads its configuration flags from environment variables injected at pod startup. A script used to update the configuration and trigger a restart encountered a timeout error on the Kubernetes API server midway through execution. It successfully restarted 3 pods but failed to restart the remaining 2. There was no validation step to ensure the configuration state was uniform across the deployment. Consequently, the cluster ran in a split-brain state, with 60% of requests applying the new tax logic and 40% applying the old legacy logic.

## Trigger
A manual execution of an outdated deployment script by a junior engineer during a routine configuration update.

## Resolution
The remaining 2 pods were manually deleted, forcing Kubernetes to recreate them with the updated environment variables. All invoices generated during the 6-hour window were flagged for review in `postgres`.

## Detection
Customer support received a ticket from a user who generated a quote (handled by an old pod) and then checked out (handled by a new pod), resulting in a mismatched final price.

## Timeline
- 2026-09-24T09:00:00Z - Script executed to update tax flag. Fails partially.
- 2026-09-24T09:05:00Z - `billing_service` begins exhibiting inconsistent behavior.
- 2026-09-24T14:30:00Z - Customer complains about quote/checkout mismatch.
- 2026-09-24T15:00:00Z - Engineering discovers the version drift among pods.
- 2026-09-24T15:10:00Z - Rogue pods terminated; consistency restored.

## Action Items
- Deprecate manual configuration scripts in favor of a GitOps approach (e.g., ArgoCD) for configuration management.
- Add an endpoint to all services that exposes the current configuration hash, and monitor for cluster drift.
- Migrate critical feature flags to a dynamic configuration service (e.g., LaunchDarkly) rather than environment variables.

## Lessons Learned
Environment variable-based configuration requires careful lifecycle management. Without automated drift detection, partial deployments can silently corrupt business data for hours before detection.
