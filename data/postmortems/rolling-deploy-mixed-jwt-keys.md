# Rolling deploy mixed JWT keys

## Date
2026-09-18

## Authors
Incident Response Team

## Status
Final

## Summary
On September 18th, 2026, a rolling deployment of the `auth_service` introduced a new JWT signing key without retaining the old one. During the 10-minute deployment window, half the fleet was issuing tokens with the new key, while the other half rejected them, causing a chaotic experience where 50% of authenticated requests failed with 401 Unauthorized.

## Impact
SEV2. For 10 minutes, users experienced intermittent failures. A user might successfully log in (hitting a new pod), but their subsequent API request would fail (hitting an old pod that didn't recognize the signature). 

## Root Cause
The `auth_service` handles JWT signing and validation. A scheduled key rotation was included in a new release. However, the configuration only contained the new key; the old key was removed from the active key set. During the rolling Kubernetes deployment, some pods ran the old version (old key) and some ran the new version (new key). Tokens signed by new pods were rejected by old pods, and tokens signed by old pods were rejected by new pods. Because the `api_gateway` load-balances requests randomly, users essentially faced a 50/50 chance of their token being accepted on any given request.

## Trigger
A routine deployment of the `auth_service` containing a hard cutover of the JWT signing key.

## Resolution
The deployment was rapidly rolled forward to 100% completion, ensuring all pods were on the new version and recognized the new key. Users who had received tokens from the old pods had to re-authenticate.

## Detection
Alerts for 401 Unauthorized errors from the `api_gateway` spiked dramatically the moment the rolling deployment began.

## Timeline
- 2026-09-18T10:00:00Z - Rolling deployment of `auth_service` begins.
- 2026-09-18T10:02:00Z - 401 error rate jumps to 25%.
- 2026-09-18T10:05:00Z - 401 error rate peaks at 50% as the deployment reaches halfway.
- 2026-09-18T10:08:00Z - Engineering identifies the mixed key issue.
- 2026-09-18T10:10:00Z - Deployment finishes; error rate drops as consistency is restored.

## Action Items
- Redesign the JWT rotation process in `auth_service` to support an overlap period (publish both keys, accept both, but sign with the new one).
- Add integration tests that specifically simulate mixed-version cluster states during deployments.
- Improve `api_gateway` handling of 401s to prompt a seamless background refresh via refresh tokens.

## Lessons Learned
Key rotation must always be a multi-step process (distribute new key -> sign with new key -> deprecate old key). Hard cutovers during a rolling deployment guarantee a split-brain validation state.
