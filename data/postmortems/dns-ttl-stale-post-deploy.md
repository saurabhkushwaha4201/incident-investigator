# DNS TTL stale post deploy

## Date
2026-09-05

## Authors
Incident Response Team

## Status
Final

## Summary
On September 5th, 2026, the `user_service` was migrated to a new Kubernetes cluster with new IP addresses. Because the DNS TTL (Time To Live) was configured to 24 hours, older DNS records remained cached by some ISPs and clients. This caused approximately 5% of API requests to hit the drained, old pods, resulting in intermittent timeouts and errors.

## Impact
SEV2. 5% of user traffic experienced severe latency and 504 Gateway Timeouts for over 12 hours as requests were routed to decommissioned infrastructure.

## Root Cause
The `api_gateway` routes some internal requests directly to the `user_service` via internal DNS. The `user_service` infrastructure was recreated, changing the underlying load balancer IP. The DNS A-record was updated, but the TTL was set to 86400 seconds (24 hours) instead of the standard 60 seconds. As a result, the `api_gateway` nodes and various external clients continued to use the cached, stale IP address. The old pods were drained and shut down shortly after the deploy, causing requests sent to the stale IP to blackhole and eventually time out.

## Trigger
A planned migration of the `user_service` to a newly provisioned node pool.

## Resolution
The old infrastructure was temporarily spun back up to gracefully handle the lingering traffic. The DNS TTL was lowered to 60 seconds, and we waited for the global caches to expire over the next 24 hours before permanently decommissioning the old cluster.

## Detection
Customer reports of intermittent extreme slowness. Dashboards showed a drop in total requests hitting the new `user_service` cluster compared to the `api_gateway` ingress metrics.

## Timeline
- 2026-09-05T01:00:00Z - `user_service` migrated; DNS updated with 24h TTL.
- 2026-09-05T01:15:00Z - Old `user_service` pods drained and terminated.
- 2026-09-05T01:30:00Z - `api_gateway` begins logging 504 Timeouts for requests hitting stale IPs.
- 2026-09-05T03:00:00Z - Engineering investigates intermittent errors.
- 2026-09-05T03:45:00Z - Stale DNS resolution identified as root cause.
- 2026-09-05T04:30:00Z - Old infrastructure restored to handle residual traffic.

## Action Items
- Audit and standardize all internal and external DNS TTLs to 60 seconds.
- Update migration runbooks to include a TTL reduction step 48 hours prior to any IP change.
- Implement synthetic monitoring that verifies resolution from multiple geographic locations.

## Lessons Learned
DNS caching is unpredictable and outside our direct control. Infrastructure migrations involving IP changes require careful TTL management and extended overlap periods for old/new systems.
