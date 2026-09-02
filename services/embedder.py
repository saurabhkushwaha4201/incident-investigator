"""
services/embedder.py — Text chunking and local embedding.

Phase 1: Priority-based splitting (paragraph → line → sentence),
         NOT a raw fixed-size cut. Almost always breaks at a natural boundary.
         Target: ~500 tokens per chunk, ~50-token overlap.

Phase 2 upgrade: Structure-aware chunking along markdown headers
         (## Timeline, ## Root Cause, ## Resolution each become chunk boundaries).
         This file will be extended in Phase 2; the Phase 1 implementation
         is the naive baseline that Phase 2's eval numbers compare against.

Embedding model: all-MiniLM-L6-v2
  - Dimension: 384 (must match EMBEDDING_DIM in models/db_models.py)
  - Runs entirely locally — no API call, no cost, no network dependency
  - ~80MB model, loads once at startup (~2-3s), then fast per-call
  - Chosen over bge-large because: no GPU required, fast on CPU,
    sufficient quality for this corpus size (~100-200 chunks total).
    bge-large (1024-dim) is documented as a Phase 2 upgrade path if
    retrieval quality proves to be the bottleneck in eval.
"""
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer

# ~500 tokens × 4 chars/token ≈ 2000 chars. Using chars as proxy since
# RecursiveCharacterTextSplitter measures in characters, not tokens.
# Overlap: ~50 tokens × 4 ≈ 200 chars — prevents content split across
# a chunk boundary from being orphaned.
CHUNK_SIZE_CHARS = 2000
CHUNK_OVERLAP_CHARS = 200

# Load once at module import time. First load downloads ~80MB; subsequent
# runs use the local HuggingFace cache (~/.cache/huggingface/).
_embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE_CHARS,
    chunk_overlap=CHUNK_OVERLAP_CHARS,
    # Priority order: paragraph break → line break → sentence → word → char.
    # Only hard-cuts mid-sentence as a last resort.
    separators=["\n\n", "\n", ". ", " ", ""],
)


def chunk_text(text: str) -> list[str]:
    """
    Split raw postmortem markdown into overlapping chunks.
    Returns at least one chunk (the full text if it fits in one chunk).
    """
    return _splitter.split_text(text)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a batch of text strings locally.
    Returns a list of 384-dimensional float vectors.
    No API call — runs entirely on CPU.
    """
    vectors = _embedding_model.encode(texts, convert_to_numpy=True)
    return vectors.tolist()
