# Route regex change 404 storm

## Date
2026-10-05

## Authors
Incident Response Team

## Status
Final

## Summary
On October 5th, 2026, a seemingly benign refactor of the routing configuration in the `api_gateway` inadvertently broke the regular expression used to match the `/v2/billing/*` API endpoints. This caused all billing-related API traffic to be misrouted or dropped, resulting in a massive storm of 404 Not Found errors and breaking all payment integrations.

## Impact
SEV1. 100% of the `/v2/billing` API surface was unavailable for 22 minutes. New subscriptions, plan changes, and automated invoice retrievals failed universally.

## Root Cause
The `api_gateway` uses a central configuration file containing complex regular expressions to map incoming URL paths to backend services. An engineer attempted to optimize the regex for the `/v2/billing` routes to correctly handle trailing slashes. However, a missing escape character in the new regex string caused the router to interpret it as a literal string rather than a pattern. Consequently, any request to endpoints like `/v2/billing/invoices` failed to match any route rule. The `api_gateway` fell back to its default behavior, returning a 404 Not Found for every billing request.

## Trigger
Deployment of `api_gateway` version 3.4.1, which contained the flawed routing configuration.

## Resolution
The `api_gateway` deployment was immediately rolled back to version 3.4.0, restoring the previous, working routing configuration.

## Detection
Synthetics monitoring for the billing endpoints immediately failed upon deployment. Simultaneously, a high-severity alert fired for a massive spike in 404 responses from the `api_gateway`.

## Timeline
- 2026-10-05T15:00:00Z - `api_gateway` version 3.4.1 deployed.
- 2026-10-05T15:01:00Z - 404 error rate for billing endpoints hits 100%.
- 2026-10-05T15:03:00Z - Synthetic monitors trigger PagerDuty alerts.
- 2026-10-05T15:15:00Z - Engineering identifies the bad regex in the recent commit.
- 2026-10-05T15:22:00Z - Gateway rolled back to 3.4.0; traffic restored.

## Action Items
- Implement strict unit testing for all route matching regular expressions in the `api_gateway` codebase.
- Add a pre-deployment validation step that tests a known set of critical URLs against the compiled routing table.
- Adopt a simpler, prefix-based routing strategy instead of complex regexes where possible.

## Lessons Learned
Global routing configurations are highly fragile. A single character mistake in a regex can bring down an entire domain of services instantly. Rigorous, automated testing of routes is mandatory.
