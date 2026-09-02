# Incident Investigator — Interview Story

> **Format:** Problem → Prior Art → My Thought Process → Phase 0 → Phase 1 → What We Learned → What's Next
> Keep this document open during a 30-minute technical interview. Sections map directly to typical interview flow.

---

## The Problem (30 seconds)

Every engineering team that runs production systems accumulates incident knowledge in a graveyard: postmortems in Confluence, runbooks in Notion, tickets in Jira. When the next incident hits at 2am, the on-call engineer needs to know if this has happened before and what fixed it.

The tools they have don't work for this:
- **Keyword search** finds "Redis" but misses "cache unavailable" or "connection refused"
- **Tribal knowledge** doesn't scale and breaks when people leave
- **Dashboards** show *that* something broke, not *why* — the "why" lives in old postmortems that dashboards don't search
- **Static runbooks** go stale and don't handle novel variations of known problems

**The gap:** there's no tool that takes an engineer's natural-language description of a new incident, surfaces the most relevant past incidents, correlates them with current logs, and proposes a root cause — with citations, and with an explicit refusal to guess when evidence is insufficient.

---

## Prior Art

This is not a replacement for Datadog/Grafana — those show metrics, this searches the text knowledge base those tools don't touch. Closest existing tools are PagerDuty's AI features and internal tools at large tech companies — none are open-source or portfolio-demonstrable.

---

## My Thought Process

### Why this project

I had already built Tollgate — a multi-tenant SaaS backend with auth, billing, rate limiting, and Stripe webhooks. That work gave me a detailed understanding of exactly how those systems *fail*. I realized I could use that knowledge as the foundation for a realistic incident corpus I could defend in detail in an interview. When an interviewer asks "is this scenario realistic?", my answer is "I designed those systems."

### Starting with the full maturity curve in mind

Before building anything, I mapped the three phases:

```
Naive RAG (Phase 1)
    → chunk text → embed → cosine similarity → LLM generation

Advanced RAG (Phase 2)
    → better chunking + BM25 hybrid + reranking + query rewriting

Agentic RAG (Phase 3)
    → multi-step reasoning + tool calls + retry loops + confidence gating
```

The decision to build all three in sequence rather than jumping to agentic was deliberate. You can't measure improvement without a baseline. You can't justify adding complexity without proving the simpler version falls short. The naive baseline is not a mistake — it's the experiment's control.

### The data problem first

Before writing a single line of RAG code, I spent time on the hardest part of any ML project: getting good data.

Key decisions made before writing any postmortems:
1. **One consistent fictional org (Tollgate)** — same service names across every incident so the log generation logic works. A service name inconsistency (`billing-service` vs `billing_service`) silently corrupts the generated logs.
2. **Single `POST /incidents` endpoint** — not a batch import script. Same endpoint used by the seed script, live demo additions, and the future UI.
3. **Logs stored as structured SQL rows, never embedded** — logs have a service, timestamp, status code, log level. These are queried with exact SQL filters, not semantic search.
4. **Three-tier eval set built before Phase 1** — clear cases, paraphrased cases (tests semantic retrieval, not keyword luck), insufficient-evidence cases (tests refusal behaviour).

---

## Phase 0: The Foundation

**What I built:** infrastructure + 22 postmortems + 35-entry eval set.

```
docker-compose up -d                    → Postgres + pgvector running
uvicorn main:app --reload               → FastAPI app, tables auto-created
python scripts/seed_incidents.py        → 22 incidents ingested directly
```

**Database state after seeding:**
```
incidents: 22 rows
chunks:    ~44-66 rows (2-3 chunks per postmortem)
logs:      ~7,000+ rows (20-80 per timeline event + INFO noise)
```

**The most defensible architectural choice:** atomic ingestion. Every `POST /incidents` commits the incident row, all chunks, and all logs in a single transaction. There is no state where you have an incident without chunks or chunks without an incident. A partial write never happens.

**The seed script evolution:** the first version of `seed_incidents.py` called `POST /incidents` over HTTP. This caused a `ReadTimeoutError` on postmortems with 9 timeline events (generating ~720 log rows + embedding took >60 seconds on CPU). The fix was to call `create_incident()` directly from `services/ingestion.py`, bypassing HTTP entirely. Better design regardless — a seed script is an ops utility, not a user request, and should not depend on a running web server.

---

## Phase 1: Naive RAG (The Baseline)

**What I built:** a working end-to-end pipeline with no optimization.

```
Query: "Users are getting charged twice for the same subscription"
    │
    ▼
Embed query → cosine similarity over ~50 chunks → top-5 chunks
    │
    ▼
[system prompt with citation rules] + [5 chunks] + [query]
    │
    ▼
Groq llama-3.1-8b-instant → answer with citations
```

**"Naive" means specifically:**
- Chunking: paragraph-based (`\n\n` split first) — doesn't know `## Root Cause` and `## Timeline` are semantically different sections
- Retrieval: pure cosine similarity — no keyword matching, no reranking
- No query rewriting — raw user query goes straight to vector search
- No log search — only prose retrieved, even though 7,000+ log rows exist

---

## Phase 1: What the Eval Actually Showed

I ran a 35-entry evaluation set against the naive pipeline. The eval methodology matters here — I made mistakes in the first version and fixed them before locking the baseline.

### The vacuous scoring bug

The first eval script handled `insufficient_evidence` queries (those with no expected document) as:
```python
found_in_top5 = len(retrieved_titles) > 0  # always True
```
pgvector always returns rows. This produced `5/5 (100%)` and a headline of `91.4%` — both meaningless. The fix was to exclude that tier entirely from the retrieval accuracy calculation and note that it belongs to Phase 3's confidence gate evaluation.

### The real numbers

| Metric | Value |
|---|---|
| Top-5 Accuracy (30 scored) | **96–97%** (range — see nondeterminism) |
| Top-1 Accuracy (30 scored) | **60.0%** |
| Paraphrased Top-5 | **100%** (8/8) |
| Paraphrased Top-1 | **12.5%** (1/8) |
| `insufficient_evidence` | NOT SCORED — deferred to Phase 3 |

### The nondeterminism finding

Between two consecutive runs on the same state, top-5 accuracy changed by 1 query. The cause: pgvector's cosine scan uses BLAS operations with sub-0.001 floating-point sensitivity. At 22 documents, multiple postmortems cluster at nearly identical cosine distances for any given query. Documents at positions 5 and 6 can swap silently between runs.

**Interview answer:** *"I found the top-5 boundary is unstable for borderline cases at this corpus size. I verified it by running the eval twice on identical state and seeing one query flip. Top-1 is more stable because those gaps are large. This is also why I didn't add an ivfflat index — an approximate index would make this worse."*

### The two specific failure patterns that motivate Phase 2

**Billing/idempotency swap (eval_001 ↔ eval_019):** Both queries describe "charged twice." The system retrieves each query's correct document in the top-5, but ranks them #1 for each other's query:
- Query about Redis fail-open → top-1 is "Idempotency window too short"
- Query about expired idempotency key → top-1 is "Billing double-charge — Redis fail-open"

Pure cosine similarity cannot distinguish these because both postmortems share vocabulary (Redis, idempotency, charged twice, billing). A cross-encoder reads query and document jointly and can distinguish "rate limiter failed during a traffic spike" from "Stripe retried an event 72 hours later."

**API Gateway attractor (eval_009, 014, 028, 029):** Four queries with completely different correct answers all retrieve `API Gateway timeout too long` as top-1. That postmortem describes "downstream service slows → gateway holds connections → gateway fails" — a semantically broad failure pattern that overlaps with any latency/connection query. Its embedding vector sits equidistant from too many queries.

**These are not vague "the model gets confused sometimes" claims.** They are specific, reproducible failure modes with exact eval IDs, queries, expected answers, and retrieved answers documented in `docs/phase1_eval_findings.md`. In an interview you can show exactly which queries failed and exactly why, not just cite a percentage.

---

## Why Phase 2 is Necessary (Not Just Nice-to-Have)

The paraphrased top-1 number tells the real story: **100% top-5 but only 12.5% top-1** on paraphrased queries.

The system finds the right document in the top-5 perfectly for every paraphrased query. But it ranks that document #1 only 1 time out of 8. The correct answer is in the context window being sent to the LLM — it's just not the first thing the LLM reads. This directly degrades generation quality because LLMs exhibit "lost in the middle" behaviour, attending to the first and last context chunks more than middle ones.

A cross-encoder reranker is not an optimization — it's the correction for a structural failure in how cosine similarity ranks semantically similar documents when vocabulary overlaps.

---

## The Full Roadmap

```
Phase 0 ✅  Data + infrastructure + eval set
Phase 1 ✅  Naive RAG baseline — measured, documented, failure modes identified
Phase 2     Hybrid search (BM25 + vector) + cross-encoder rerank + RAGAS eval
Phase 3     LangGraph agent — router, retry loop, confidence gate, log tool
Phase 3.5   React + Vite UI — live agent step trace via SSE streaming
Phase 4     LangSmith tracing + prompt injection defense + RBAC
```

**What the full project proves (one line each):**
- **Phase 1–2:** I can build and *measurably improve* a retrieval pipeline, including finding and fixing bugs in my own evaluation methodology
- **Phase 3:** I can build multi-step agentic reasoning with an explicit uncertainty gate
- **Phase 4:** I understand production concerns specific to LLM systems — tracing, prompt injection, output leakage, access control
- **Overall:** this is the AI engineering complement to Tollgate, and both draw from the same real systems

---

## Appendix: Technical Decisions I Can Defend

| Decision | Why |
|---|---|
| `all-MiniLM-L6-v2` for Phase 1 | Correct for a baseline. pgvector column dimension is immutable — commit to `bge-large` only after Phase 2 eval shows a quality bottleneck |
| pgvector over Pinecone | Same Postgres instance, free, exact cosine at this corpus size |
| No `ivfflat` index | Approximate index on 22 docs with `lists=100` hurts accuracy. Sequential scan is 1-5ms and exact. Also found sub-0.001 cosine distance clustering — approximation would make nondeterminism worse |
| Exclude `insufficient_evidence` from retrieval eval | No retrieval metric is defined for null-expected-doc queries. Forcing `len(results) > 0` produces a vacuous 100% that proves nothing |
| Seed script calls `create_incident()` directly | No HTTP timeout risk, no dependency on uvicorn, faster — a seed script is an ops utility not a user request |
| UUID PKs | Safe to generate client-side, no ordering leakage, industry standard |
| Atomic ingestion transaction | No orphaned incidents, no orphaned chunks |
| Single `POST /incidents` endpoint | Same code path for seed script, live demo, and future UI. No separate batch import |
