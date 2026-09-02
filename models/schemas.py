"""
models/schemas.py — Pydantic request/response models for FastAPI endpoints.

These are separate from the SQLAlchemy ORM models (models/db_models.py).
SQLAlchemy models describe DB table structure.
Pydantic models describe HTTP request/response shapes and handle validation.
"""
from pydantic import BaseModel


class TimelineEvent(BaseModel):
    """One entry in an incident timeline."""
    time: str   # ISO8601 string, e.g. "2026-07-15T14:30:00Z"
    event: str  # Human-readable description of what happened


class IncidentCreateRequest(BaseModel):
    """
    Payload for POST /incidents.
    This is the single ingestion shape — used by both the seed script
    and any future live additions (no separate batch-load format).
    """
    title: str
    postmortem_body: str        # Full markdown following docs/postmortem_template.md
    service_tags: list[str]     # Must match architecture.md service names exactly
    timeline: list[TimelineEvent]


class IncidentCreateResponse(BaseModel):
    """Response from POST /incidents on success."""
    status: str       # Always "indexed"
    incident_id: str  # UUID string


class QueryRequest(BaseModel):
    """Payload for POST /query."""
    query: str
    k: int = 5        # Number of chunks to retrieve


class QueryResponse(BaseModel):
    """Response from POST /query."""
    answer: str
    cited_incidents: list[str]  # List of incident UUID strings
