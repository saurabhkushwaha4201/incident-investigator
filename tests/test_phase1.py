"""
tests/test_phase1.py — Core unit + integration tests for Phase 1.

Run with: pytest tests/test_phase1.py -v

Requires a separate test database (uses TEST_DATABASE_URL, never touches
the dev database incident_investigator):
  docker exec -it <container> psql -U postgres
  > \\c incident_investigator_test
  > CREATE EXTENSION IF NOT EXISTS vector;
  (This is handled automatically by db/init.sql on container first start.)

The db_session fixture creates tables fresh per test and drops them after —
every test runs against a clean, isolated DB state.
"""
import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from models.db_models import Chunk, LogEntry
from services.embedder import chunk_text, embed_texts
from services.ingestion import create_incident
from services.retrieval import retrieve_top_k

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/incident_investigator_test",
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def db_session():
    """
    Per-test database session against the test DB.
    Creates all tables before the test and drops them after — clean slate.

    NOTE: In a multi-file test suite this fixture would live in conftest.py
    so pytest shares it automatically. Single test file → defined here,
    functionally equivalent.
    """
    test_engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    session = TestSession()
    yield session
    session.close()
    Base.metadata.drop_all(test_engine)


# ---------------------------------------------------------------------------
# Sample postmortems (used across multiple tests)
# ---------------------------------------------------------------------------

SAMPLE_POSTMORTEM = """# Incident: Billing double-charge — Redis fail-open

**Date:** 2026-07-15
**Service(s) affected:** api_gateway, redis, billing_service
**Severity:** SEV1

## Timeline
- 14:30 UTC — Redis connection pool exhausted; health checks start failing
- 14:32 UTC — Rate limiter circuit breaker opens and fails open
- 14:33 UTC — Stripe webhook retry received; idempotency check bypassed (Redis down)
- 14:34 UTC — Second charge posted to customer; alert fired

## Root Cause
Redis became unreachable during a traffic spike that exhausted the connection pool.
The api_gateway rate limiter was configured to fail open when Redis is unavailable,
and billing_service's idempotency check also depended on Redis for deduplication.
When a Stripe webhook retry arrived during the Redis outage, the idempotency key
lookup returned a cache miss, causing the webhook to be processed a second time
and a duplicate charge to be posted.

## Resolution
Added a database-level idempotency check in billing_service as a fallback when
Redis is unavailable. Updated the rate limiter to fail closed by default.

## Related Services/Errors
redis timeout, rate_limiter fail-open, billing_service idempotency, 503, duplicate charge
"""

UNRELATED_POSTMORTEM = """# Incident: Auth service DNS misconfiguration

**Date:** 2026-07-18
**Service(s) affected:** auth_service
**Severity:** SEV3

## Timeline
- 09:10 UTC — Deployment of auth_service v2.4.1 completed
- 09:11 UTC — Login failure alerts triggered
- 09:13 UTC — Root cause identified: DNS record not updated
- 09:14 UTC — DNS record corrected; logins recover

## Root Cause
A misconfigured DNS record caused auth_service to be unreachable for three
minutes following a deployment. The deploy script updated the service's IP
but the DNS TTL had not expired, causing some requests to route to the old pod.

## Resolution
Updated the deploy runbook to flush DNS cache after IP changes.
Added a post-deploy smoke test that verifies auth_service is reachable via DNS.

## Related Services/Errors
auth_service, dns, 404, deployment
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_chunk_text_produces_at_least_one_chunk():
    chunks = chunk_text(SAMPLE_POSTMORTEM)
    assert len(chunks) >= 1
    assert all(isinstance(c, str) and len(c) > 0 for c in chunks)


def test_embed_texts_returns_correct_dimension():
    """Embedding dimension must match EMBEDDING_DIM in models/db_models.py (384)."""
    vectors = embed_texts(["a test sentence about redis connection timeouts"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 384, (
        f"Expected 384-dim embedding (all-MiniLM-L6-v2), got {len(vectors[0])}. "
        "If you switched to bge-large, update EMBEDDING_DIM in models/db_models.py to 1024."
    )


def test_create_incident_persists_chunks_and_logs(db_session):
    """
    create_incident() must atomically write chunks (from postmortem)
    and logs (from timeline) — both linked to the same incident_id.
    """
    incident = create_incident(
        db=db_session,
        title="Billing double-charge — Redis fail-open",
        postmortem_body=SAMPLE_POSTMORTEM,
        service_tags=["billing_service", "redis", "api_gateway"],
        timeline=[
            {"time": "2026-07-15T14:30:00Z", "event": "Redis connection pool exhausted"},
            {"time": "2026-07-15T14:32:00Z", "event": "Rate limiter failed open"},
        ],
    )

    chunks = db_session.query(Chunk).filter_by(incident_id=incident.id).all()
    logs = db_session.query(LogEntry).filter_by(incident_id=incident.id).all()

    assert len(chunks) > 0, "Postmortem body must produce at least one chunk"
    assert len(logs) > 0, "Timeline events must produce synthetic log rows"
    assert all(log.incident_id == incident.id for log in logs)


def test_incident_without_timeline_still_ingests_postmortem(db_session):
    """Logs are generated from the timeline — an empty timeline produces zero logs.
    Postmortem chunking must not depend on logs existing."""
    incident = create_incident(
        db=db_session,
        title="Minimal incident",
        postmortem_body="## Root Cause\nSomething broke in api_gateway.",
        service_tags=["api_gateway"],
        timeline=[],
    )
    chunks = db_session.query(Chunk).filter_by(incident_id=incident.id).all()
    logs = db_session.query(LogEntry).filter_by(incident_id=incident.id).all()

    assert len(chunks) > 0
    assert len(logs) == 0


def test_retrieval_finds_semantically_relevant_chunk_despite_different_wording(db_session):
    """
    Core semantic-search assertion: a query using different vocabulary than
    the source document should still retrieve it — unlike keyword search.

    This is one of the explicit value propositions of the system (from PRD
    Section 1: 'Keyword search misses paraphrased problems').
    """
    create_incident(
        db=db_session,
        title="Billing double-charge — Redis fail-open",
        postmortem_body=SAMPLE_POSTMORTEM,
        service_tags=["billing_service", "redis"],
        timeline=[],
    )
    create_incident(
        db=db_session,
        title="Auth service DNS misconfiguration",
        postmortem_body=UNRELATED_POSTMORTEM,
        service_tags=["auth_service"],
        timeline=[],
    )

    # Deliberately avoids exact words from SAMPLE_POSTMORTEM ("Redis", "idempotency", "fail-open")
    results = retrieve_top_k(db_session, "customers were charged twice for one order", k=1)

    assert len(results) == 1
    # The billing/redis postmortem should surface, not the auth/DNS one
    top_chunk = results[0]["chunk_text"].lower()
    assert any(kw in top_chunk for kw in ["charge", "billing", "redis", "idempoten", "duplicate"]), (
        f"Expected billing-related chunk, got: {results[0]['chunk_text'][:200]}"
    )


def test_retrieve_top_k_respects_k_limit(db_session):
    """retrieve_top_k must return at most k results even if more chunks exist."""
    for i in range(5):
        create_incident(
            db=db_session,
            title=f"Incident {i}",
            postmortem_body=f"## Root Cause\nGeneric failure number {i} in api_gateway.",
            service_tags=["api_gateway"],
            timeline=[],
        )

    results = retrieve_top_k(db_session, "generic failure in gateway", k=3)
    assert len(results) == 3


def test_create_incident_returns_valid_uuid(db_session):
    """incident.id must be a valid UUID (not an integer SERIAL)."""
    import uuid
    incident = create_incident(
        db=db_session,
        title="UUID check incident",
        postmortem_body="## Root Cause\nTest for UUID primary key.",
        service_tags=["api_gateway"],
        timeline=[],
    )
    # Should not raise — if id were an integer this would ValueError
    parsed = uuid.UUID(str(incident.id))
    assert parsed.version == 4
