# Incident Investigator — Product Requirements Document & Build Roadmap

**Author:** Saurabh Kumar
**Stack:** Python, FastAPI, PostgreSQL + pgvector, Redis
**Goal:** A single production-grade project demonstrating the full RAG maturity curve — naive → advanced → agentic — plus evaluation, observability, and security, built around a real, defensible use case.

---

## 1. Problem Statement

When a production incident happens (a service errors out, a metric spikes, a customer complains), on-call engineers currently rely on:
- Keyword search over Confluence/wikis — misses paraphrased problems ("charged twice" vs. "double-charged")
- Tribal knowledge — "ask the person who handled it last time," doesn't scale, breaks when people leave
- Static runbook checklists — go stale, don't handle novel variations of a known problem
- Dashboards (Datadog/Grafana) — good at showing *something* broke, bad at explaining *why*, because the "why" usually lives in unstructured text (old postmortems, tickets) that dashboards don't search

**Incident Investigator** is a tool that ingests an organization's own incident history (postmortems, runbooks, structured logs) and, given a new incident description, retrieves the most relevant past context, correlates it with structured log data, and proposes a root cause and fix — with citations, and with an explicit refusal to answer when evidence is insufficient.

This is not a replacement for alerting/monitoring tools. It's a semantic search + reasoning layer over an org's accumulated incident knowledge, which those tools don't provide.

---

## 2. Users & Scope

- **User:** an on-call engineer, single team/org (no multi-tenancy — that's deliberately out of scope; it's already proven in a prior project)
- **Interaction:** engineer types a natural-language incident description → gets back likely root cause, supporting evidence, similar past incidents, and a confidence level
- **Out of scope for v1:** live log ingestion from a real production system, multi-org SaaS billing, real-time alerting integration
- **Single fictional org framing:** all data in the system belongs to one consistent fictional organization running Tollgate-like infrastructure, using a fixed, reused set of service names (`auth_service`, `billing_service`, `api_gateway`, `redis`, etc.) across every postmortem and log entry. Public postmortems are never dropped in verbatim — they're used only as inspiration for realistic failure *patterns*, rewritten into this org's context with its own service names. Verbatim external incidents can't have matching logs generated for them (you don't know AWS's real architecture), and mixing "real AWS outage" next to "our org's outage" breaks the internal consistency the whole corpus depends on.

---

## 3. Data Sources

| Source | Type | How it's obtained |
|---|---|---|
| Self-authored postmortems (15–20) | Prose | Written by hand, modeled on realistic failures in a prior project (Redis fail-open, webhook idempotency, token replay, rate-limit misconfig) |
| Public postmortems | Prose | VOID (Verica Open Incident Database), AWS/GitHub/Cloudflare public status-page writeups |
| Runbooks | Prose, semi-structured | Hand-authored, paired with the postmortems above |
| Synthetic logs | Structured | Scripted generation of timestamped log lines matching each incident scenario |

**Design decision:** logs are never embedded. They're stored as structured rows in Postgres and queried with SQL filters (time range, service, error code). Only prose (postmortems, runbooks) is chunked and embedded into the vector store. This is stated explicitly because it's a common mistake (embedding everything) and correcting it is itself a signal of understanding when *not* to use vector search.

---

## 4. Architecture Overview

```
User query (incident description)
        │
        ▼
   Query Router  ──────► decides: runbook/postmortem search | log search | both
        │
        ▼
 ┌──────────────┬───────────────────┐
 │ Vector search │   SQL log search  │
 │ (pgvector)    │   (Postgres)      │
 └──────┬────────┴─────────┬─────────┘
        ▼                  ▼
   Cross-encoder rerank (on prose results)
        │
        ▼
  Evidence correlation (agent step)
        │
        ▼
  Confidence check ──► low ──► "insufficient evidence, here's what's missing"
        │
       high
        ▼
  Root cause + fix + citations
```

All steps traced end-to-end (LangSmith/Phoenix), with RBAC gating who can trigger live tool calls, and guardrails on ingested/generated content.

---

## 5. Phase Breakdown

### Phase 0 — Setup & Data Prep (Week 1)
- Repo scaffold: FastAPI app, Postgres + pgvector via Docker Compose, `.env` config
- `db/database.py` — DB connection/engine setup only (SQLAlchemy session factory, `get_db()` dependency). Note: this is **not** a login/auth session — auth/RBAC doesn't appear until Phase 4. Naming it `database.py` rather than `session.py` avoids that confusion.
- Write/collect 20–25 postmortems + matching runbooks, using a consistent markdown template (`## Timeline`, `## Root Cause`, `## Resolution`, `## Related Services/Errors`) — structure matters because Phase 2's structure-aware chunking and the log-generation logic both depend on it
- Build a 30–40 question eval set: three tiers — ~25 "clear" cases, ~10 "paraphrased" cases (tests real semantic retrieval, not keyword luck), ~5 "insufficient evidence" cases (tests the Phase 3 confidence gate) — this is your ground truth for every later phase

**Data generation approach (revised to avoid burnout):** hand-write 5–10 postmortems fully yourself, modeled directly on Tollgate's real architecture (Redis fail-open + rate limiter, webhook HMAC mismatch, refresh-token replay, idempotent-upsert failure under concurrency) — these anchor your understanding and are the ones you should be able to defend in detail in an interview. Adapt the remaining 10–15 from public incident patterns (VOID, `kubernetes-failure-stories`, cloud status-page writeups), **rewritten into the fictional org's context and service names** — never copied verbatim. If generating drafts with a strong model (GPT-4o/Claude) against a strict prompt locking in your architectural constraints, personally review and correct every one for technical accuracy. Never ship a postmortem you couldn't explain unprompted.

**Ingestion is continuous, not a one-time batch load.** Don't build a folder-walking script that runs once. Instead, build a single API endpoint that both the initial seed data and every future addition go through:

```
POST /incidents
{
  "title": "Billing double-charge — Redis fail-open",
  "postmortem_body": "<markdown, following the template above>",
  "service_tags": ["billing_service", "redis"],
  "timeline": [
    {"time": "2026-08-29T14:30:00Z", "event": "Redis connection pool exhausted"},
    {"time": "2026-08-29T14:32:00Z", "event": "Rate limiter failed open"}
  ]
}
```

On each call, the endpoint: (1) chunks + embeds the postmortem body and inserts it into pgvector, and (2) generates matching synthetic log rows clustered around each timeline timestamp (plus background `INFO` noise so log search isn't trivial) and inserts them into the `logs` table — both tagged with the same `incident_id` so a retrieved chunk can always be joined back to its exact log window. Postmortem and logs are added atomically as one "incident record," never as two independent, potentially-orphaned submissions. Your initial load of 20–25 postmortems is just this same endpoint called 20–25 times via a small seed script — one ingestion code path reused for both initial load and every later addition, which also directly enables live-demo additions in the Phase 3.5 UI.

**Why evaluation ground truth isn't a separate problem to solve:** a postmortem's own `## Root Cause` section, written when the incident is authored/adapted, *is* the ground truth answer — it records what really happened, the same way a postmortem does in real life (written after the true cause was confirmed). Your eval set's `expected_source_doc` and `expected_root_cause_keywords` point back to that section. This isn't circular: eval queries deliberately use different wording than the postmortem ("charged twice" vs. the doc's "duplicate webhook processing"), which is what actually tests semantic retrieval rather than memorization.

**Deliverable:** clean corpus + eval set + working `POST /incidents` endpoint committed to repo, nothing else built yet.

---

### Phase 1 — Basic (Naive) RAG (Week 2)
**Goal:** working end-to-end loop, no optimization.

- **Chunking:** priority-based splitting (e.g. `RecursiveCharacterTextSplitter`) — tries paragraph breaks (`\n\n`) first, then line breaks, then sentence boundaries, only hard-cutting mid-sentence as a last resort. Target ~500 tokens per chunk (measured via the embedding model's own tokenizer, not word count), ~50-token overlap between consecutive chunks so content split across a boundary isn't orphaned. This is the naive baseline on purpose — a timeline entry or list can still get awkwardly split; Phase 2 fixes this with structure-aware (markdown-header-based) chunking instead.
- **Embeddings:** `sentence-transformers`, using `bge-large` (or `bge-m3`) rather than the smaller `all-MiniLM-L6-v2` — both are free and local (no API cost, no network call, run entirely on your machine), but `bge-large` is meaningfully higher quality on retrieval benchmarks and the corpus here is small enough (a few hundred chunks) that the extra model size/embedding time is not a real cost. Document this comparison explicitly in the write-up (why bge over MiniLM, given corpus size) rather than defaulting silently.
- **Vector store:** pgvector (self-hosted Postgres extension — free, and you already know Postgres)
- **Retrieval:** top-k cosine similarity search
- **Generation:** LLM call (Groq free tier or Gemini free tier for dev; keep provider swappable via a thin interface) with retrieved chunks stuffed into the prompt, instructed to cite the source document
- **API:** `POST /query` → returns answer + cited doc IDs

**Alternatives considered:**
| Choice | Alternative | Why this pick |
|---|---|---|
| pgvector | Pinecone / Weaviate / Qdrant | Free, no external service, you already run Postgres |
| bge-large (local) | all-MiniLM-L6-v2 (local) | Also free; bge is higher quality and the corpus is small enough that the size/speed cost is negligible |
| sentence-transformers (either model) | OpenAI/Cohere embeddings (paid API) | Local avoids per-call cost and network latency during heavy re-embedding iteration; reserved as a documented comparison experiment later if Phase 2 eval shows retrieval quality plateauing |
| Groq/Gemini free tier | Claude/OpenAI paid | Zero cost during heavy dev iteration; swap in a paid model later for the final demo if desired |

**Deliverable:** a working `/query` endpoint that answers from postmortems with citations. No reranking, no hybrid search yet — this is the baseline you'll measure improvement against.

---

### Phase 2 — Advanced RAG (Weeks 3–4)
**Goal:** materially better retrieval accuracy, and a real evaluation harness proving it.

- **Structure-aware chunking:** upgrade from Phase 1's priority-based splitting to chunking along the postmortem's actual markdown sections (`## Timeline`, `## Root Cause`, `## Resolution` each become their own chunk boundary), storing section type as metadata on each chunk. Fixes Phase 1's failure mode where a list (e.g. a timeline) could get arbitrarily sliced mid-entry. This is one of the concrete, measurable improvements to show in the before/after eval table.
- **Structured log retrieval — parameterized tool calling, not text-to-SQL:** the LLM never writes raw SQL. Define a strict Pydantic schema (`service: Enum`, `time_window_minutes: int`, `status_code: Optional[int]`, `log_level: Optional[Enum]`) as a LangChain `@tool`'s `args_schema`. The LLM fills out this schema as structured JSON; your Python backend builds the parameterized query and executes it. If the LLM proposes an invalid enum value (e.g. a nonexistent service), Pydantic raises a validation error before the query ever runs — LangGraph can feed that error back to the LLM as a retry signal. This eliminates SQL hallucination entirely and gives Phase 3's retry loop a genuine, demoable recovery case.
- **Query rewriting:** LLM step that turns a messy incident description ("payments doubled") into technical search terms ("idempotent usage-metering," "ON CONFLICT upsert failure") before vector search
- **Hybrid search:** combine BM25 (keyword, via `rank_bm25` or Postgres full-text search) with vector search over the prose corpus — catches exact terms (error codes, service names) that embeddings alone sometimes miss
- **Reranking:** cross-encoder (e.g. `cross-encoder/ms-marco-MiniLM-L-6-v2`, free/local) reorders combined BM25+vector results before they reach the LLM
- **Evaluation:** run RAGAS (faithfulness, answer relevance, context precision/recall) against your 30–40 question eval set; also compute a simpler direct metric — "did the correct root-cause document appear in top-k retrieved" — since that's the number that actually matters here
- **RAGAS data-shape buffer (important):** RAGAS expects a specific HuggingFace `Dataset` structure (`question`, `answer`, `contexts`, `ground_truth`), and mismatched shapes throw cryptic errors before judging even starts. Budget 2–3 extra days in this phase for a small translation script converting your Postgres/LangGraph outputs into that exact format. Don't fight this by hand repeatedly — write the converter once and reuse it for every eval run.
- **Judge model separation (important):** RAGAS's scores are only as reliable as the LLM used to judge them. Do not use a small free model (Groq/Llama) as the RAGAS judge — scores will be noisy and won't hold up under questioning. Stick to `gpt-4o-mini` or `gpt-4o` specifically for judging — RAGAS integrates with OpenAI natively with zero adapter friction, unlike Claude, which requires digging into LangChain adapter code for no real benefit. Keep Groq/Gemini for runtime answer generation (free, fast, fine for demo traffic); the judge call runs infrequently (once per eval run, not per user query), so the OpenAI cost is a few cents.
- **Hybrid fusion — skip manual RRF math:** don't hand-write Reciprocal Rank Fusion SQL to normalize BM25's `ts_rank` against pgvector's cosine distance — those scales don't compare cleanly and the SQL gets ugly fast for no real payoff. Instead: retrieve top-20 via BM25 and top-20 via vector search separately, union and dedupe the two lists in Python (~30–40 candidates), and hand that combined set straight to the Phase 2 cross-encoder reranker, which rescores everything on one consistent scale. This is simpler to build and a cleaner thing to defend in an interview than manually tuned fusion weights.
- **Baseline comparison:** record Phase 1 (naive) scores vs. Phase 2 (hybrid+rerank) scores — this before/after table is one of your strongest interview artifacts

**Alternatives considered:**
| Choice | Alternative | Why this pick |
|---|---|---|
| BM25 + vector hybrid | Vector-only | Exact error codes/service names are better matched by keyword search; hybrid catches both |
| Cross-encoder rerank | No rerank | Reranking is cheap and the eval numbers will show a measurable lift — worth demonstrating explicitly |
| RAGAS | DeepEval / custom eval | RAGAS is the most recognized name for a resume/interview, well-documented |

**Deliverable:** hybrid retrieval pipeline + a documented eval report (numbers, not just claims) showing improvement over Phase 1.

---

### Phase 3 — Agentic RAG (Weeks 5–6)
**Goal:** multi-step investigation instead of single-shot Q&A, with an honest confidence gate.

- **Framework:** LangGraph — stateful graph lets you build retry/re-query loops (e.g. "found a Redis error mention, now check for related past incidents") rather than a single linear chain
- **Router node:** classifies whether the query needs runbook search, log search, past-incident search, or a combination
- **Investigation loop:** agent retrieves initial evidence → self-checks sufficiency → if insufficient, issues a follow-up retrieval (different query angle or the other data source) → caps at N iterations to avoid infinite loops
- **Confidence gating (headline feature):** the agent must output a confidence level; below a defined threshold, it responds with what evidence is missing rather than asserting a guessed root cause. This is the most defensible, most "AI engineering senior signal" part of the whole project — build and test it carefully, and demo it explicitly with a "not enough evidence" example, not just successful cases.
- **State bloat control:** every tool call (log search, runbook search) appends its output to LangGraph's shared state. In a retry loop that queries 2-3 times, this can bloat to 30,000+ tokens by the time it reaches the LLM, risking a context-length error or "lost in the middle" behavior where the model ignores the most relevant evidence buried in a wall of stale tool output. Implement a state reducer that runs before each LLM call: keep only the most recent tool execution plus the final reranked evidence, and drop or summarize older intermediate outputs rather than accumulating everything.

**Alternatives considered:**
| Choice | Alternative | Why this pick |
|---|---|---|
| LangGraph | Plain function-calling loop / CrewAI / AutoGen | LangGraph gives explicit state + cyclic graphs, which is the cleanest way to show a real retry/investigation loop rather than a single tool-call chain |
| Confidence threshold via self-reported LLM score | Separate calibration model | Simpler to build and explain; note in write-up that a trained calibration model would be the production upgrade |

**Deliverable:** `/investigate` endpoint that runs the multi-step graph and returns either a grounded answer with confidence, or an explicit "insufficient evidence" response listing what's missing.

---

### Phase 3.5 — Lightweight UI (Days: 2–3, run alongside Phase 4)
**Goal:** make the system demoable live, without over-investing in frontend polish.

A backend-only API is hard to present in a 30-minute interview. Build a minimal single-page React view (Vite, not a full Next.js app) with:
- An input box for the incident description
- A live-updating panel showing the agent's step trace as it runs: routing decision → tool call issued (with the actual Pydantic-validated JSON args) → evidence retrieved → confidence check → final answer

This step-trace panel is the highest-value UI element in the whole project — it turns the confidence-gating feature (Phase 3's headline feature) from something you describe verbally into something an interviewer watches happen in real time. Time-box this to 2–3 days; the RAG/agent depth is the point of the project, not frontend polish, so resist the temptation to build a full dashboard.

**Streaming is not optional here.** An agentic LangGraph run with retries can take 15–30 seconds end-to-end. If the FastAPI endpoint blocks until the full graph finishes and returns one large JSON payload, the UI will sit frozen for that entire window — which reads as a crash in a live demo, the worst possible moment for it to happen. Use `StreamingResponse` (Server-Sent Events) on the backend, driven by LangGraph's native `.astream_events()`, which emits events (`tool_start`, `tool_end`, node transitions) as they happen. The frontend consumes this event stream to update the step-trace panel in real time rather than waiting on a single final response.

---

### Phase 4 — Observability & Security (Weeks 7–8)
**Goal:** make the system debuggable and defensible in production terms.

**Observability:**
- LangSmith or Arize Phoenix tracing on every request: router decision → retrieval (vector + SQL) → rerank scores → agent loop iterations → confidence check → final answer
- Latency breakdown per step (useful for explaining performance trade-offs in an interview)

**Security:**
- **Prompt injection defense:** ingested postmortems/logs could contain adversarial text (especially if this were ever user-submitted) — basic detection/sanitization before ingestion, plus instructing the LLM to treat retrieved context as data, not instructions
- **Output scrubbing:** PII/secret detection on generated answers (real logs sometimes leak tokens/emails — catching this is a legitimate, non-decorative feature given the domain)
- **RBAC:** JWT-based, two roles — `on-call` (can trigger live log search + full investigation) and `viewer` (read-only runbook search). Reuses the auth pattern from your prior project, but here it's the access-control layer for an AI system, not a plain API — worth stating that distinction explicitly.

**Alternatives considered:**
| Choice | Alternative | Why this pick |
|---|---|---|
| LangSmith | Phoenix (Arize) / custom logging | LangSmith has the lowest setup friction for LangGraph specifically |
| Simple RBAC (2 roles) | Full multi-tenant permission system | Multi-tenancy is already proven elsewhere; keep this project focused on AI-specific security, not repeated backend scope |

**Deliverable:** trace dashboard screenshots/recording, a documented threat model (what you defend against and why), and RBAC enforced at the middleware layer.

---

## 6. Tech Stack Summary

| Layer | Choice | Free-tier notes |
|---|---|---|
| API framework | FastAPI | — |
| DB / vector store | PostgreSQL + pgvector | Self-hosted, free |
| Embeddings | sentence-transformers, `bge-large` (local) | Free, no API quota concerns; higher quality than MiniLM at negligible extra cost for this corpus size |
| Reranker | cross-encoder (local, MiniLM) | Free |
| Keyword search | BM25 / Postgres full-text | Free |
| LLM (dev) | Groq / Gemini free tier | Provider-agnostic interface so it's swappable |
| Agent framework | LangGraph | Free (open source) |
| Tracing | LangSmith or Phoenix | Free tier sufficient for portfolio scale |
| Eval | RAGAS | Free (open source) |
| Auth | JWT (reuse pattern from prior project) | — |
| Log tool interface | Pydantic + LangChain `@tool` (parameterized, not text-to-SQL) | Free, eliminates SQL hallucination by construction |
| Hybrid fusion | Python-side union + dedupe → cross-encoder rerank | Avoids hand-written RRF math normalizing BM25/pgvector score scales in SQL |
| RAGAS judge model | `gpt-4o-mini` / `gpt-4o` (paid, judge-only) | Native OpenAI integration, zero adapter friction; a few cents per eval run |
| Agent state management | LangGraph state reducer (truncate old tool outputs) | Prevents context-length errors and "lost in the middle" behavior on retry loops |
| UI streaming | FastAPI `StreamingResponse` (SSE) + LangGraph `.astream_events()` | Keeps the step-trace UI responsive during 15–30s agentic runs instead of appearing frozen |
| UI | React + Vite (lightweight, not full dashboard) | Leverages existing React experience; scoped to 2–3 days |

---

## 7. Evaluation Plan (what to actually measure and show)

1. **Retrieval accuracy:** % of eval questions where the correct source document appears in top-k, compared Phase 1 vs. Phase 2
2. **RAGAS scores:** faithfulness, answer relevance, context precision — reported as a before/after table
3. **Root-cause accuracy:** on your ground-truth eval set, % of cases where the system's proposed root cause matches the known correct one
4. **Confidence calibration:** on a held-out set of "genuinely ambiguous" incidents (deliberately under-specified), confirm the system says "insufficient evidence" rather than guessing — this is a qualitative but very demoable check
5. **Latency breakdown:** from tracing, average time per pipeline stage

---

## 8. Interview Narrative (what this project proves, in one line each)

- **Phase 1–2:** I can build and *measurably improve* a retrieval pipeline, not just call an embedding API
- **Phase 3:** I can build multi-step agentic reasoning with an explicit uncertainty gate, not a system that always confidently answers
- **Phase 4:** I understand production concerns specific to LLM systems — tracing, prompt injection, output leakage, access control — not just app-level security
- **Overall:** this project is the "AI engineering" complement to a prior "backend engineering" project (multi-tenant SaaS, auth, billing, rate limiting), and both draw from the same real systems, which makes the two-project portfolio read as coherent rather than disconnected

---

## 9. Suggested Timeline

| Week | Focus |
|---|---|
| 1 | Data prep, eval set, repo scaffold |
| 2 | Phase 1 — naive RAG working end-to-end |
| 3–4 | Phase 2 — hybrid search (Python-side fusion + rerank), RAGAS harness (+2-3 day data-shape buffer), before/after report |
| 5–6 | Phase 3 — LangGraph agent, router, confidence gating, parameterized log tool |
| 6–7 | Phase 3.5 — lightweight UI with live agent step-trace |
| 7–8 | Phase 4 — tracing, security, RBAC, RAGAS judge run, final polish + demo write-up |

Total: ~8 weeks at a steady part-time pace; compressible to 5–6 weeks if focused full-time.
