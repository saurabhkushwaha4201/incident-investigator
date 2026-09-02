"""
models/db_models.py — SQLAlchemy ORM table definitions.

Design decisions:
  - Logs are NEVER embedded. They live in `logs`, queried via SQL filters
    (time range, service, status code). Only postmortem prose gets embedded.
  - `incident_id` on Chunk and LogEntry links a chunk back to its source incident
    so retrieved chunks can be joined to their exact log window.
  - `section_type` on Chunk is nullable in Phase 1 (fixed-size chunking doesn't
    populate it). Phase 2's structure-aware chunker fills it with 'timeline',
    'root_cause', 'resolution', etc.
  - UUIDs for all primary keys — consistent with the established Phase 1 schema.
  - EMBEDDING_DIM = 384 matches all-MiniLM-L6-v2. If you switch to bge-large,
    change this to 1024 AND drop+recreate the chunks table (vector dimension is
    baked into the column type and cannot be altered in place).
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, Integer, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from db.database import Base

# Change to 1024 if switching to bge-large-en-v1.5.
# Must match the model used in services/embedder.py.
EMBEDDING_DIM = 384


class Incident(Base):
    """
    One 'incident record' = one postmortem + its matching synthetic logs,
    created atomically via POST /incidents.
    All chunks and logs reference this record via incident_id.
    """
    __tablename__ = "incidents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    postmortem_body = Column(Text, nullable=False)
    service_tags = Column(JSON, nullable=False, default=list)
    timeline = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)

    chunks = relationship("Chunk", back_populates="incident", cascade="all, delete-orphan")
    logs = relationship("LogEntry", back_populates="incident", cascade="all, delete-orphan")


class Chunk(Base):
    """
    A chunked, embedded piece of a postmortem's prose.
    This is what vector similarity search operates on.
    Logs are never stored here — see LogEntry.
    """
    __tablename__ = "chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False)
    chunk_text = Column(Text, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    # Populated starting Phase 2 (structure-aware chunking along markdown headers).
    # Values: 'timeline', 'root_cause', 'resolution', 'related_errors', or None.
    section_type = Column(String, nullable=True)
    embedding = Column(Vector(EMBEDDING_DIM), nullable=False)

    incident = relationship("Incident", back_populates="chunks")


class LogEntry(Base):
    """
    A structured synthetic log row generated to match an incident's timeline.
    Queried via SQL filters (time window, service, log level, status code).
    Never embedded — embedding log lines is a common mistake; SQL filters
    are the right tool for structured, timestamped data.
    """
    __tablename__ = "logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    service = Column(String, nullable=False, index=True)
    level = Column(String, nullable=False)      # INFO | WARN | ERROR | FATAL
    status_code = Column(Integer, nullable=True)
    message = Column(Text, nullable=False)

    incident = relationship("Incident", back_populates="logs")
