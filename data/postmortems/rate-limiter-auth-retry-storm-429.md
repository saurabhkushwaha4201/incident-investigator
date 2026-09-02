# Rate limiter auth retry storm 429

## Date
2026-08-28

## Authors
Incident Response Team

## Status
Final

## Summary
On August 28th, 2026, a logic error in our mobile SDK caused it to enter a tight retry loop when the `auth_service` returned a specific token refresh error. This flood of retries quickly hit the per-IP rate limit in the `api_gateway`, resulting in legitimate users on shared IP addresses (like corporate NATs or public Wi-Fi) receiving 429 Too Many Requests errors.

## Impact
SEV2. Mobile users on updated SDK versions, as well as innocent bystanders sharing their IP addresses, were completely blocked from using the API for up to an hour. 

## Root Cause
The `api_gateway` implements rate limiting backed by `redis` based on the client's IP address. A bug in the new mobile SDK caused it to retry token refresh requests infinitely without backoff if the `auth_service` returned a 400 Bad Request. When a single mobile client entered this loop, it blasted the `api_gateway` with thousands of requests per second. The `api_gateway` correctly applied the rate limit and began returning 429s. However, because the limit is per-IP, all other users behind the same NAT gateway were also blocked.

## Trigger
A new version of the mobile SDK was released to the app stores.

## Resolution
The rate limiting rules in the `api_gateway` were temporarily adjusted to rate limit by user ID instead of IP address for authenticated routes. The mobile SDK was patched to implement exponential backoff and to stop retrying on 4xx errors.

## Detection
A sudden spike in 429 errors from the `api_gateway` triggered an alert. Simultaneously, customer support received complaints from users in a specific corporate office who could not access the app.

## Timeline
- 2026-08-28T09:00:00Z - Mobile SDK v1.5 released.
- 2026-08-28T10:30:00Z - Clients hit the refresh error and begin retry storm.
- 2026-08-28T10:35:00Z - `api_gateway` begins rate-limiting heavily; 429 alerts fire.
- 2026-08-28T11:00:00Z - Engineering correlates the IPs to corporate networks and identifies the SDK bug.
- 2026-08-28T11:30:00Z - `api_gateway` rate limiting rules adjusted to mitigate IP blocking.

## Action Items
- Release hotfix for mobile SDK to fix retry logic.
- Enhance `api_gateway` rate limiting to combine IP and device fingerprinting for more granular blocking.
- Add specific monitoring for token refresh endpoint failure rates.

## Lessons Learned
IP-based rate limiting is a blunt instrument that can easily cause collateral damage during a client-side retry storm. SDKs must strictly adhere to backoff protocols.
