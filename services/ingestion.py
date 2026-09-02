"""
services/ingestion.py — Incident creation: chunking, embedding, log generation.

create_incident() is the single ingestion code path used by:
  - POST /incidents (both live additions and seed script)
  - No other ingestion path exists (no folder-walker, no batch script).

All three DB writes (incident row, chunks, logs) happen in one transaction —
an incident record is atomic. A partial write (postmortem without logs, or
vice versa) is never committed.

Log generation design:
  - _LOG_MESSAGE_TEMPLATES defines realistic log messages per service.
  - For each timeline event, a burst of ERROR/WARN logs is generated
    clustered around the event timestamp (±30s).
  - Background INFO noise is added (~10% of error volume, ±300s) so
    log retrieval in Phase 2 isn't trivially easy (can't just grep for ERROR).
  - Only services in _LOG_MESSAGE_TEMPLATES have bespoke log messages.
    Services NOT in the dict (user_service, postgres) fall back to api_gateway
    templates silently — this is noted in docs/architecture.md.
"""
import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from models.db_models import Incident, Chunk, LogEntry
from services.embedder import chunk_text, embed_texts

# =============================================================================
# Log message templates — one entry per known service.
# Keys MUST match the service names in docs/architecture.md exactly.
# If you add a service, add a key here AND update architecture.md.
# =============================================================================
_LOG_MESSAGE_TEMPLATES: dict[str, list[str]] = {
    "redis": [
        "Redis connection timeout after 5000ms",
        "ECONNREFUSED 127.0.0.1:6379",
        "Redis connection pool exhausted (max=50)",
        "Redis command failed: read timeout",
        "Circuit breaker OPEN: Redis unreachable",
        "Idempotency key lookup failed: Redis unavailable",
    ],
    "billing_service": [
        "Upstream 503 from billing_service",
        "Webhook processing failed: duplicate event detected",
        "ON CONFLICT upsert retry attempt",
        "Stripe webhook signature verification failed",
        "Usage metering insert conflict — concurrent write",
        "Idempotency check skipped: Redis unavailable",
        "Billing reconciliation job timeout",
    ],
    "auth_service": [
        "JWT validation failed: signature mismatch",
        "Refresh token reuse detected — invalidating session",
        "401 Unauthorized on protected route",
        "Session invalidated: token replay attack suspected",
        "Token refresh rate limit exceeded",
        "JWT exp check failed: clock skew detected",
    ],
    "api_gateway": [
        "Rate limiter fail-open triggered — Redis unavailable",
        "Upstream timeout after 30000ms",
        "502 Bad Gateway from upstream",
        "429 Too Many Requests — rate limit hit",
        "Route not found: 404",
        "Request queue at 95% capacity",
        "Circuit breaker HALF-OPEN: probing upstream",
    ],
    "user_service": [
        "User profile fetch timeout",
        "Database connection pool at limit",
        "Stale data returned from read replica",
        "Account status check failed: downstream error",
    ],
    "postgres": [
        "Connection pool exhausted: all 10 connections in use",
        "Query timeout after 30000ms",
        "Deadlock detected — rolling back transaction",
        "Replica lag: 45s behind primary",
        "Max connections reached: new connections rejected",
    ],
}

_BACKGROUND_NOISE_MESSAGES = [
    "Health check OK",
    "Request completed in 42ms",
    "Cache hit — serving from Redis",
    "Scheduled job finished successfully",
    "Webhook received and acknowledged",
    "Token refresh completed",
    "DB connection acquired from pool",
]


def create_incident(
    db: Session,
    title: str,
    postmortem_body: str,
    service_tags: list[str],
    timeline: list[dict],
) -> Incident:
    """
    Create one incident record atomically:
      1. Insert the incident row
      2. Chunk + embed the postmortem body → insert chunks
      3. Generate synthetic log rows → insert logs
    All three are committed together or not at all.
    """
    incident = Incident(
        title=title,
        postmortem_body=postmortem_body,
        service_tags=service_tags,
        timeline=timeline,
    )
    db.add(incident)
    db.flush()  # assign incident.id before creating FK-dependent rows

    _ingest_postmortem_chunks(db, incident)
    _generate_matching_logs(db, incident)

    db.commit()
    db.refresh(incident)
    return incident


def _ingest_postmortem_chunks(db: Session, incident: Incident) -> None:
    """Chunk the postmortem body, embed each chunk, persist to `chunks` table."""
    chunks = chunk_text(incident.postmortem_body)
    vectors = embed_texts(chunks)
    for idx, (text, vector) in enumerate(zip(chunks, vectors)):
        db.add(Chunk(
            incident_id=incident.id,
            chunk_text=text,
            chunk_index=idx,
            section_type=None,  # Phase 1: naive chunking, no section metadata.
                                 # Phase 2 structure-aware chunking fills this in.
            embedding=vector,
        ))


def _generate_matching_logs(
    db: Session,
    incident: Incident,
    rows_per_event: tuple[int, int] = (20, 80),
) -> None:
    """
    For each timeline event, generate a burst of synthetic log rows clustered
    around its timestamp, plus low-rate background INFO noise.

    The noise ensures log retrieval (Phase 2) can't trivially filter by ERROR
    level alone — the signal is correct logs tied to the right incident_id,
    mixed with plausible-looking background traffic.
    """
    services = incident.service_tags or ["api_gateway"]

    for event in incident.timeline:
        event_time = event["time"]  # ISO8601 string
        service = random.choice(services)
        templates = _LOG_MESSAGE_TEMPLATES.get(
            service,
            _LOG_MESSAGE_TEMPLATES["api_gateway"]  # fallback for unlisted services
        )
        n_rows = random.randint(*rows_per_event)

        # Error/warning burst around the event timestamp
        for _ in range(n_rows):
            offset_seconds = random.randint(-30, 30)
            db.add(LogEntry(
                incident_id=incident.id,
                timestamp=_parse_and_offset(event_time, offset_seconds),
                service=service,
                level=random.choice(["ERROR", "ERROR", "WARN", "FATAL"]),  # weighted toward ERROR
                status_code=random.choice([500, 502, 503, None]),
                message=random.choice(templates),
            ))

        # Background INFO noise (~10% of error volume, wider time window)
        for _ in range(max(1, n_rows // 10)):
            offset_seconds = random.randint(-300, 300)
            db.add(LogEntry(
                incident_id=incident.id,
                timestamp=_parse_and_offset(event_time, offset_seconds),
                service=random.choice(services),
                level="INFO",
                status_code=200,
                message=random.choice(_BACKGROUND_NOISE_MESSAGES),
            ))


def _parse_and_offset(iso_timestamp: str, offset_seconds: int) -> datetime:
    """Parse an ISO8601 timestamp string and apply a second-level offset."""
    base = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return base + timedelta(seconds=offset_seconds)
