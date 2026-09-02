"""
routers/query.py — POST /query endpoint.

Phase 1: naive top-k retrieval → LLM generation with retrieved context.
         No reranking, no hybrid search — this is the baseline.

Phase 2 upgrade: hybrid BM25 + vector retrieval, cross-encoder reranking.
                 This file will be extended; Phase 1 implementation
                 is the documented baseline for before/after eval comparison.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db.database import get_db
from models.schemas import QueryRequest, QueryResponse
from services.retrieval import retrieve_top_k
from services.llm import generate, build_query_prompt, INVESTIGATOR_SYSTEM_PROMPT, Role

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
def query_incidents(payload: QueryRequest, db: Session = Depends(get_db)):
    """
    Given an incident description:
      1. Embed the query and retrieve top-k similar postmortem chunks
      2. Build a context-stuffed prompt
      3. Call the LLM (Groq runtime model) for a grounded, cited answer
    Returns the answer and the incident UUIDs cited.
    """
    retrieved = retrieve_top_k(db, payload.query, k=payload.k)
    prompt = build_query_prompt(payload.query, retrieved)
    answer = generate(prompt, role=Role.RUNTIME, system=INVESTIGATOR_SYSTEM_PROMPT)

    return QueryResponse(
        answer=answer,
        cited_incidents=list({c["incident_id"] for c in retrieved}),
    )
