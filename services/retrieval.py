"""
services/retrieval.py — Vector similarity search over postmortem chunks.

Phase 1: Naive top-k cosine similarity (no reranking, no hybrid search).
         This is the baseline Phase 2 measures improvement against.

Phase 2 additions (in a new retrieval_v2.py or extended here):
  - BM25 / Postgres full-text search for keyword matching
  - Python-side union + dedupe of BM25 and vector results (~40 candidates)
  - Cross-encoder reranking over the combined set

No pgvector index (ivfflat) is used. Rationale:
  - ivfflat is an *approximate* nearest-neighbor index — it trades accuracy
    for speed at scale.
  - At ~100-200 chunks (20-25 incidents), a full sequential scan is:
    (a) fast: ~1-5ms — negligible
    (b) exact: correct cosine similarity, no approximation error that would
        quietly skew eval numbers for reasons unrelated to RAG quality.
  - ivfflat with lists=100 on <1000 rows partitions poorly and degrades
    accuracy. Add it only if corpus grows beyond ~10k chunks, and tune
    lists = ceil(rows / 1000) at that point.
"""
from sqlalchemy.orm import Session

from models.db_models import Chunk
from services.embedder import embed_texts


def retrieve_top_k(db: Session, query: str, k: int = 5) -> list[dict]:
    """
    Embed the query and return the k most similar postmortem chunks
    by cosine distance (ascending = most similar first).

    Returns a list of dicts:
        {"incident_id": str, "chunk_text": str, "chunk_index": int}
    """
    query_vector = embed_texts([query])[0]

    results = (
        db.query(Chunk)
        .order_by(Chunk.embedding.cosine_distance(query_vector))
        .limit(k)
        .all()
    )

    return [
        {
            "incident_id": str(c.incident_id),
            "chunk_text": c.chunk_text,
            "chunk_index": c.chunk_index,
        }
        for c in results
    ]
