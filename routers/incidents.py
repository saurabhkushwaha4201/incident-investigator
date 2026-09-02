"""
routers/incidents.py — POST /incidents endpoint.

This is the single ingestion path for all incident data:
  - Used by the seed script (scripts/seed_incidents.py) for the initial load
  - Used for any future live additions via the Phase 3.5 UI
  - No separate batch-load path exists

On each call the endpoint atomically:
  1. Chunks + embeds the postmortem body → inserts into `chunks` (pgvector)
  2. Generates matching synthetic log rows → inserts into `logs`
  3. Returns incident_id on success
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db.database import get_db
from models.schemas import IncidentCreateRequest, IncidentCreateResponse
from services.ingestion import create_incident

router = APIRouter()


@router.post("/incidents", response_model=IncidentCreateResponse)
def add_incident(payload: IncidentCreateRequest, db: Session = Depends(get_db)):
    """
    Ingest one incident: chunk + embed the postmortem, generate synthetic logs.
    All DB writes are atomic — partial writes are never committed.
    """
    incident = create_incident(
        db=db,
        title=payload.title,
        postmortem_body=payload.postmortem_body,
        service_tags=payload.service_tags,
        timeline=[e.model_dump() for e in payload.timeline],
    )
    return IncidentCreateResponse(status="indexed", incident_id=str(incident.id))
