# Phase 1 — Naive RAG: Deep Dive

> This is the baseline that all later phases measure improvement against.
> The word "naive" is not a criticism — it is intentional. A naive baseline
> is the scientific control in your experiment.

---

## What Phase 1 Delivers

| Feature | Status |
|---|---|
| `RecursiveCharacterTextSplitter` paragraph-first chunking | ✅ Done |
| `all-MiniLM-L6-v2` local embeddings (384-dim, free, no API) | ✅ Done |
| pgvector cosine similarity top-k retrieval | ✅ Done |
| Groq (llama-3.1-8b-instant) answer generation | ✅ Done |
| Citation-enforcing system prompt | ✅ Done |
| `POST /query` endpoint returns answer + cited incident IDs | ✅ Done |
| 7 pytest tests all passing (see §9 for itemized list) | ✅ Done |
| Modular file split (services/, routers/, models/) | ✅ Done |
| `scripts/run_eval.py` — retrieval accuracy baseline | ✅ Done |

---

## 1. Chunking Strategy

### What we implemented
`services/embedder.py` uses LangChain's `RecursiveCharacterTextSplitter`:

```python
_splitter = RecursiveCharacterTextSplitter(
    chunk_size=500 * 4,      # ~500 tokens in chars (rough approximation)
    chunk_overlap=50 * 4,    # ~50 token overlap between consecutive chunks
    separators=["\n\n", "\n", ". ", " ", ""],
)
```

### How `RecursiveCharacterTextSplitter` works
It tries each separator in order. First it tries `\n\n` (paragraph breaks). If a paragraph is still too large, it tries `\n` (line breaks). Then `. ` (sentence boundaries). Only as a last resort does it hard-cut mid-sentence. For a well-formatted postmortem, virtually every split happens at `\n\n` — the paragraph/section boundary.

### Why ~500 tokens with 50-token overlap
- **500 tokens:** A single `## Root Cause` section paragraph fits comfortably in 500 tokens.
- **50-token overlap:** If a critical sentence falls at the chunk boundary, the overlap ensures it appears in both chunks — so neither is orphaned.

### Known Phase 1 limitation (intentional)
A timeline list might get split mid-list if it's long. Phase 2's structure-aware chunker fixes this by treating `## Timeline` as a single indivisible chunk boundary.

> **Interview question:** *Why not just use a fixed character count (hard cut at 2000 chars)?*
> Hard cuts break semantic coherence. The recursive strategy tries to keep meaningful units together, which directly affects retrieval quality.

---

## 2. Embedding Model

### What we implemented
`all-MiniLM-L6-v2` from `sentence-transformers`:
- **Dimension:** 384 (stored in pgvector as `Vector(384)`)
- **Runs:** locally on CPU, no API call, no cost, no network latency
- **Speed:** ~100ms to embed a batch of chunks

### Why not `bge-large` (which the PRD originally mentioned)?
The PRD listed `bge-large` as preferred, but we chose `all-MiniLM-L6-v2` for Phase 1:
- `bge-large` produces 1024-dim vectors. The `chunks` table's `Vector(N)` column is **dimension-immutable** in pgvector — switching models means `DROP TABLE chunks` and re-embedding everything.
- Phase 1 is the *baseline* (not the optimized version), so the simpler/faster model is correct.
- The eval numbers will tell us whether the quality upgrade to `bge-large` is worth the migration cost.

### EMBEDDING_DIM as a single constant
```python
# models/db_models.py
EMBEDDING_DIM = 384
```
Defined once, used everywhere. The test `assert len(vectors[0]) == 384` catches any silent model change immediately.

---

## 3. Vector Store and Retrieval

### What we implemented
```python
def retrieve_top_k(db: Session, query: str, k: int = 5) -> list[dict]:
    query_vector = embed_texts([query])[0]
    results = (
        db.query(Chunk)
        .order_by(Chunk.embedding.cosine_distance(query_vector))
        .limit(k)
        .all()
    )
```

### Why cosine distance?
Cosine similarity measures the angle between vectors, ignoring magnitude. Two sentences that mean the same thing produce vectors pointing in the same direction regardless of sentence length. Dot product conflates direction with magnitude; L2 is sensitive to scale.

### Why no pgvector `ivfflat` index?
`ivfflat` is an **approximate** nearest-neighbor index. At 22 documents with `lists=100`, it would partition poorly and degrade accuracy. A sequential scan takes ~1-5ms at this scale and is exact. More importantly: discovered during evaluation that cosine distances cluster at sub-0.001 separations for related documents. An approximate index would make this floating-point sensitivity *worse*, not better.

> Add `ivfflat` only when corpus grows beyond ~10,000 chunks, tuning `lists = ceil(rows / 1000)`.

---

## 4. LLM Generation Layer

### What we implemented
`services/llm.py` provides a single `generate()` function with a `Role` enum:

```python
class Role(str, Enum):
    RUNTIME = "runtime"   # Groq/Llama — free, used for answering queries
    JUDGE   = "judge"     # OpenAI GPT-4o-mini — reliable, for RAGAS eval only
```

Swapping the runtime model (Groq → Gemini) is a `.env` change, not a code change.

### The citation-enforcing system prompt
```
Rules:
1. Answer ONLY using the context provided.
2. Every factual claim must cite [incident: <id>].
3. If context is insufficient, say "Insufficient evidence" and list what's missing. Do not guess.
4. Be concise.
```

Rule 3 seeds Phase 3's confidence gate. The LLM's refusal-to-guess behaviour starts here, even before Phase 3 formalises it into a structured confidence score.

---

## 5. Modular Structure vs. Single File

The prototype (`docs/incident_investigator_phase1.py`) is 569 lines in one file. Our production implementation splits it:

```
services/embedder.py    ← chunking + embedding
services/llm.py         ← all LLM calls
services/ingestion.py   ← create_incident(), log generation
services/retrieval.py   ← retrieve_top_k()
models/db_models.py     ← SQLAlchemy ORM models
models/schemas.py       ← Pydantic request/response schemas
routers/incidents.py    ← POST /incidents
routers/query.py        ← POST /query
db/database.py          ← engine + get_db()
main.py                 ← FastAPI app assembly
```

This structure signals you've worked on real codebases. Phase 2 additions sit next to Phase 1 files without touching the originals, making the before/after comparison visible in the file structure.

---

## 6. The Seed Script — Why It Calls `create_incident()` Directly

The first version of `seed_incidents.py` called `POST /incidents` over HTTP. This caused a `ReadTimeoutError` after 60 seconds because embedding + generating 700+ log rows for a postmortem with 9 timeline events takes longer than that on CPU.

The fix: the seed script calls `create_incident()` directly from `services/ingestion.py`, bypassing HTTP entirely:

```
Old: Python script → HTTP → uvicorn → create_incident()
New: Python script → create_incident() directly
```

Benefits:
- No timeout risk
- No dependency on uvicorn running during seeding
- Faster (no network overhead per incident)

This is correct architecture: the seed script is a dev/ops utility, not a user-facing request. It should not be gated on a running web server.

---

## 7. The Eval Script — Scoring Logic and What Was Fixed

`scripts/run_eval.py` measures retrieval accuracy by checking whether each eval query's `expected_source_doc` appears in the top-5 retrieved incidents.

### What was wrong in the first version
The first version handled `insufficient_evidence` queries (those with `expected_source_doc: null`) as:
```python
found_in_top5 = len(retrieved_titles) > 0   # always True — pgvector never returns 0 rows
```
This reported `5/5 (100%)` for that tier and polluted the headline denominator (35 instead of 30), producing a vacuously inflated `91.4%`.

### The fix
`insufficient_evidence` queries have no defined retrieval metric. Their correct system behaviour is a *generation* refusal, not a *retrieval* match. They are excluded from the accuracy calculation entirely, with a note deferring them to Phase 3.

### What the final eval script reports
- **Scored tiers:** `clear` and `paraphrased` only (30 queries)
- **Per-query verbose output** for all misses: exact query, expected title, full top-5 retrieved list
- **Not scored:** `insufficient_evidence` — explicitly noted with reason

---

## 8. Measured Baseline Results

**See `docs/phase1_eval_findings.md` for the full analysis.** Summary:

| Metric | Value |
|---|---|
| Top-5 Accuracy | 96–97% (29-30/30) — range due to cosine distance nondeterminism |
| Top-1 Accuracy | 60.0% (18/30) — stable |
| `clear` Top-5 | 95.5% (21/22) |
| `paraphrased` Top-1 | 12.5% (1/8) — the reranker's entire job |
| `insufficient_evidence` | NOT SCORED |

### The two failure patterns that motivate Phase 2

**1. Billing/idempotency swap (eval_001 ↔ eval_019):** Both queries describe "charged twice." The embedding model cannot distinguish "Redis failed open during a spike" from "Stripe idempotency key expired after 72 hours" because both documents share the same vocabulary. They return each other's correct answer as top-1.

**2. API Gateway attractor (eval_009, 014, 028, 029):** Four queries with different correct answers all retrieve `API Gateway timeout too long` as top-1. That postmortem describes a broad failure pattern (downstream slows → gateway fails) that overlaps with any latency/connection query.

A cross-encoder reranker reads the full query and document text together, which is what is needed to distinguish these cases. Pure cosine similarity structurally cannot.

---

## 9. The 7 Pytest Tests (Itemized)

All 7 in `tests/test_phase1.py`. Each test runs against a **clean, isolated test DB** (`incident_investigator_test`) — no dev data is touched.

| Test | What it checks |
|---|---|
| `test_chunk_text_produces_at_least_one_chunk` | Chunker doesn't silently return an empty list on a valid postmortem body |
| `test_embed_texts_returns_correct_dimension` | Embedding model produces 384-dim vectors; fails loudly if model is swapped to `bge-large` without updating `EMBEDDING_DIM` |
| `test_create_incident_persists_chunks_and_logs` | Atomic write: after one `create_incident()` call, both chunks and logs exist in the DB, all linked to the same `incident_id` |
| `test_incident_without_timeline_still_ingests_postmortem` | Empty timeline → 0 logs, but chunking still completes — log generation must not block postmortem ingestion |
| `test_retrieval_finds_semantically_relevant_chunk_despite_different_wording` | Core semantic-search assertion: query using different vocabulary than the source document still retrieves the correct postmortem (keyword search would fail this) |
| `test_retrieve_top_k_respects_k_limit` | `retrieve_top_k(db, query, k=3)` returns exactly 3 results even if >3 chunks exist |
| `test_create_incident_returns_valid_uuid` | `incident.id` is a valid v4 UUID — not an integer SERIAL; `uuid.UUID(str(incident.id))` must not raise |

---

## 10. Phase 1 End-to-End Latency (Measured)

Measured on `POST /query` against a 22-incident corpus on a CPU-only dev machine:

| Step | Time |
|---|---|
| Embed query (`all-MiniLM-L6-v2`) | ~285ms |
| pgvector cosine scan (22 incidents, ~50 chunks) | ~325ms |
| **Subtotal (retrieval only)** | **~610ms** |
| Groq `llama-3.1-8b-instant` generation | ~800–1500ms |
| **Total `POST /query` round-trip** | **~1.4–2.1s** |

> The 285ms embed time is dominated by model load on first call (the model is cached after that). Subsequent calls in the same process are ~5-20ms. The CI pipeline and seed script don't pay this cost per call after warmup.

**Why this matters for Phase 3.5:** Phase 1 is ~1.5s end-to-end. Phase 3's agentic loop (router → tool calls → retry → confidence check → generation) takes 15–30s. A UI that blocks on a 30s request is unusable. This is exactly why Phase 3.5 introduces SSE streaming — the user sees each agent step as it completes rather than waiting for the full response.

---

## 11. Open Question for Phase 3 Kickoff

**How will the confidence score be computed?**

The Phase 3 confidence gate decides whether the retrieved evidence is strong enough to answer or whether the system should refuse and ask for more information. Three candidate approaches, not yet decided:

1. **Retrieval score threshold:** refuse if top-1 cosine similarity < threshold (e.g., < 0.65). Simple, no extra compute, but doesn't account for ambiguity between top-1 and top-2.
2. **Score gap heuristic:** refuse if `cos(top1) - cos(top2) < delta`. Catches cases where two documents are nearly equidistant (i.e., the system is genuinely uncertain between two hypotheses).
3. **LLM self-rated confidence:** include a structured output field where the LLM rates its own certainty 1–5. Adds latency and is poorly calibrated (LLMs tend to over-report confidence).

The right answer is probably a combination of (1) and (2). This is an open design question to be resolved at Phase 3 kickoff, not assumed to be already decided.

| PRD Requirement | Our Implementation | Status |
|---|---|---|
| `RecursiveCharacterTextSplitter`, paragraph-first | `_splitter` in `services/embedder.py` | ✅ |
| ~500 token chunks, ~50 token overlap | `chunk_size=2000, chunk_overlap=200` (char approximation) | ✅ |
| Local embedding model (free, no API) | `all-MiniLM-L6-v2` via sentence-transformers | ✅ |
| pgvector cosine similarity top-k | `cosine_distance` in `retrieve_top_k()` | ✅ |
| LLM generation via Groq (free tier) | `services/llm.py` with `RUNTIME` role → Groq | ✅ |
| Provider-swappable via config | `RUNTIME_LLM_PROVIDER` env var | ✅ |
| `POST /query` → answer + cited doc IDs | `routers/query.py` | ✅ |
| Citation-enforcing system prompt | `INVESTIGATOR_SYSTEM_PROMPT` in `services/llm.py` | ✅ |
| No reranking, no hybrid search yet (baseline) | Only cosine similarity | ✅ |
| Eval set run against Phase 1 before Phase 2 | `scripts/run_eval.py`, results in `phase1_eval_findings.md` | ✅ |

---

## 10. What Phase 1 Cannot Do (and Why That's Fine)

| Limitation | Root cause | Phase 2 fix |
|---|---|---|
| Misses "read replica" (eval_022) | Embeddings are semantic; "stale data" ≠ "read replica" in vector space | BM25 hybrid search catches exact terms |
| Billing/idempotency confusion | Two postmortems share vocabulary, vectors cluster together | Cross-encoder reranker reads jointly |
| API Gateway attractor pattern | Broad failure description matches many queries | Cross-encoder reranker distinguishes mechanisms |
| Top-1 only 60% despite 97% top-5 | Correct doc found but not ranked first | Cross-encoder reranker |
| No log search | Only prose retrieved | Parameterized SQL log tool (Phase 3) |
| No confidence score | Single-shot LLM call | LangGraph confidence gate (Phase 3) |
