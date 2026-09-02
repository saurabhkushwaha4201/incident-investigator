# Thread leak OOM billing

## Date
2026-09-28

## Authors
Incident Response Team

## Status
Final

## Summary
On September 28th, 2026, a thread leak in the `billing_service` caused it to gradually consume all available memory over a 6-hour period. This resulted in Out-Of-Memory (OOM) kills by the container orchestrator, leading to dropped API usage events and failed webhooks until the underlying bug was patched.

## Impact
SEV2. Over 6 hours, approximately 15% of `billing_service` requests failed as pods were continuously killed and restarted. This caused delays in subscription processing and required a massive retry effort from the `api_gateway`.

## Root Cause
The `billing_service` introduced a new background worker pool to handle secondary analytics processing. This worker pool was designed to spawn a new goroutine for every incoming request but lacked proper error handling. When an downstream dependency briefly returned 500 errors, the goroutines blocked indefinitely waiting on a channel that would never receive a signal. As traffic continued, the number of blocked threads skyrocketed. The Go runtime allocated stack space for each thread, slowly inflating the memory footprint until it exceeded the 2GB container limit, causing an OOM kill.

## Trigger
A minor, 2-minute outage of an internal analytics dependency triggered the error condition that leaked the threads.

## Resolution
The `billing_service` deployment was temporarily rolled back to a previous version that lacked the new analytics worker pool. Once stability was restored, a fix was developed to ensure channels were properly closed using `defer` statements, and strict context timeouts were added to all goroutines.

## Detection
Memory utilization alerts for the `billing_service` slowly trended upwards over 6 hours, followed by a sudden spike in Kubernetes OOMKilled events and pod restarts.

## Timeline
- 2026-09-28T04:00:00Z - Minor analytics dependency fails for 2 minutes.
- 2026-09-28T04:02:00Z - `billing_service` threads begin leaking and blocking.
- 2026-09-28T07:00:00Z - Memory utilization crosses 80% threshold; warning alerts fire.
- 2026-09-28T10:00:00Z - First pod hits 2GB limit and is OOM killed.
- 2026-09-28T10:15:00Z - Pod restart storm begins; error rates spike.
- 2026-09-28T10:45:00Z - Engineering identifies thread leak and rolls back `billing_service`.

## Action Items
- Enforce strict `context.Context` timeouts on all goroutines in the `billing_service`.
- Add active monitoring for goroutine counts, not just overall memory usage.
- Review all channel usage for potential deadlocks or orphaned writers.

## Lessons Learned
Memory leaks in garbage-collected languages often manifest as unbounded concurrency (thread/goroutine leaks). Relying solely on OOM kills for recovery masks the underlying issue until it becomes a cascading failure.
