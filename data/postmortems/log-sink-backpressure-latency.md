# Log sink backpressure latency

## Date
2026-09-20

## Authors
Incident Response Team

## Status
Final

## Summary
On September 20th, 2026, the centralized logging agent experienced severe queue congestion. Because our core services were configured to write logs synchronously without a proper timeout, the backpressure propagated up to the `api_gateway`, `auth_service`, and `billing_service`, causing latency spikes that degraded the entire platform.

## Impact
SEV2. Platform-wide API latency increased from an average of 150ms to over 8 seconds. This caused timeouts for many external integrations and frustrated users, lasting for 1 hour and 15 minutes.

## Root Cause
All services forward logs to a local logging agent (DaemonSet) which ships them to an external observability vendor. The vendor experienced a partial outage, causing the logging agent's internal queue to fill up. Once the queue was full, the logging agent stopped accepting new bytes. Crucially, the logging libraries used in `api_gateway` and `billing_service` were configured in a blocking mode. When they attempted to write standard INFO logs (e.g., "Request received"), the write system call blocked indefinitely. This tied up application threads just waiting to print logs, starving the services of resources to actually process user requests.

## Trigger
A network routing issue at our external observability vendor caused them to drop incoming log traffic.

## Resolution
The logging agent configuration was dynamically updated to drop logs (fail open) when its queue reached 80% capacity. Once applied, the blocking write calls completed (by discarding the logs), and application latency immediately returned to normal.

## Detection
P99 latency alerts triggered across all services simultaneously. Curiously, CPU and memory utilization were very low, indicating threads were blocked on I/O.

## Timeline
- 2026-09-20T13:00:00Z - External vendor begins experiencing ingest issues.
- 2026-09-20T13:10:00Z - Logging agent queues fill up locally.
- 2026-09-20T13:12:00Z - Platform latency spikes to 8+ seconds.
- 2026-09-20T14:00:00Z - Engineering traces the blocked threads to the logging library.
- 2026-09-20T14:15:00Z - Logging agent configured to drop logs on backpressure; latency recovers.

## Action Items
- Reconfigure all application logging libraries to strictly use asynchronous, non-blocking I/O.
- Configure the logging agent to aggressively drop logs rather than block upstream processes.
- Implement separate monitoring for the logging agent queue depth.

## Lessons Learned
Observability infrastructure is on the critical path if not carefully decoupled. Synchronous logging is a massive vulnerability that allows non-critical telemetry failures to take down core business logic.
