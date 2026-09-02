# 🔍 Incident Investigator

[![CI Pipeline](https://github.com/saurabhkushwaha4201/incident-investigator/actions/workflows/ci.yml/badge.svg)](https://github.com/saurabhkushwaha4201/incident-investigator/actions/workflows/ci.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/release/python-3110/)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=flat&logo=postgresql&logoColor=white)](https://www.postgresql.org/)

> **Semantic search + reasoning over an engineering team's incident history.**  
> Given a new incident description, surfaces relevant past postmortems, proposes a root cause with citations, and explicitly refuses to guess when evidence is insufficient.

---

## ⚡ The Problem

When production incidents happen, on-call engineers search their team's postmortem history for similar past incidents. Existing tools fail at this:

- **Keyword search** misses paraphrased problems ("charged twice" ≠ "duplicate webhook processing")  
- **Tribal knowledge** doesn't scale and breaks when people leave
- **Dashboards** show *that* something broke, not *why*

This project builds a semantic layer over an org's incident knowledge base — ingesting postmortems and synthetic logs, answering natural-language queries with grounded root-cause analysis and citations.

---

## 🚀 Quick Start

```bash
# 1. Start Postgres + pgvector
docker-compose up -d

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Add your GROQ_API_KEY to .env

# 4. Start the app
uvicorn main:app --reload

# 5. Seed the corpus (first time only)
python scripts/seed_incidents.py

# 6. Run tests
pytest tests/test_phase1.py -v
```

---

## 🌐 API Usage

### 1. Ingest an incident
```http
POST /incidents
Content-Type: application/json

{
  "title": "Billing double-charge — Redis fail-open",
  "postmortem_body": "## Root Cause\nRedis became unreachable...",
  "service_tags": ["api_gateway", "redis", "billing_service"],
  "timeline": [
    {"time": "2026-07-15T14:30:00Z", "event": "Redis connection pool exhausted"},
    {"time": "2026-07-15T14:32:00Z", "event": "Rate limiter failed open"}
  ]
}
```
**What happens:** the endpoint atomically chunks + embeds the postmortem body, generates 20-80 synthetic log rows per timeline event (clustered ±30s), and commits everything in a single transaction.

### 2. Query the system
```http
POST /query
Content-Type: application/json

{"query": "Users are reporting they were charged twice for the same order", "k": 5}
```
**Response:**
```json
{
  "answer": "Based on incident [incident: abc-123], the root cause was Redis becoming unreachable...",
  "cited_incidents": ["abc-123"]
}
```

---

## 🏗️ Architecture

```text
POST /incidents
    │
    ├─ chunk_text()     RecursiveCharacterTextSplitter (paragraph-first)
    ├─ embed_texts()    all-MiniLM-L6-v2, 384-dim, local (no API call)
    ├─ INSERT chunks    pgvector Vector(384) column
    └─ INSERT logs      20-80 ERROR/WARN rows per timeline event + INFO noise

POST /query
    │
    ├─ embed_texts([query])
    ├─ cosine_distance() top-k over chunks table (exact scan, no ivfflat index)
    └─ Groq llama-3.1-8b-instant → answer with citations
```

**Key design decisions:**
- **Logs are never embedded** — stored as structured SQL rows, queried with filters (Phase 2+)
- **Ingestion is atomic** — postmortem + chunks + logs committed in one transaction
- **No ivfflat index** — exact sequential scan at this corpus size (~200 chunks) is 1-5ms and correct
- **One ingestion code path** — `POST /incidents` is used by the seed script, live demo additions, and future UI

---

## 📂 Project Structure

```text
├── main.py                    FastAPI app, router registration, create_all()
├── routers/
│   ├── incidents.py           POST /incidents
│   └── query.py               POST /query
├── services/
│   ├── embedder.py            Chunking + local embedding
│   ├── ingestion.py           create_incident() — atomic write
│   ├── retrieval.py           retrieve_top_k() — cosine similarity
│   └── llm.py                 Groq/OpenAI/Gemini provider interface
├── models/
│   ├── db_models.py           SQLAlchemy: Incident, Chunk, LogEntry
│   └── schemas.py             Pydantic request/response shapes
├── db/
│   ├── database.py            Engine + get_db() dependency
│   └── init.sql               CREATE EXTENSION vector (runs once)
├── data/
│   ├── postmortems/           22 .md + .json pairs
│   └── eval_set.json          35 eval entries (clear / paraphrased / insufficient_evidence)
├── docs/
│   ├── architecture.md        Canonical Tollgate service names + constraints
│   ├── postmortem_template.md Exact section headers required by Phase 2 chunker
│   ├── phase0_deep_dive.md    Phase 0 learning guide + interview Q&A
│   ├── phase1_deep_dive.md    Phase 1 learning guide + interview Q&A
│   ├── phase1_eval_findings.md Phase 1 baseline retrieval eval results
│   └── interview_story.md     Full project narrative for interviews
├── scripts/
│   ├── seed_incidents.py      Calls POST /incidents for all 22 postmortems
│   └── run_eval.py            Evaluates vector retrieval against baseline
└── tests/
    └── test_phase1.py         7 pytest tests (all passing)
```

---

## 📚 Corpus

**22 postmortems** covering realistic failure modes in a fictional SaaS org (Tollgate):

| Category | Examples |
|---|---|
| 🔴 **Redis** | Fail-open double-charge, circuit breaker flapping, async queue eviction |
| 🟡 **Auth** | Refresh token replay, JWT clock skew, rolling deploy key mismatch |
| 🟢 **Billing** | HMAC mismatch silent drop, idempotent upsert race, idempotency window |
| 🔵 **Infra** | Postgres pool exhaustion, thundering herd, DNS TTL stale, TLS cert expiry |
| 🟣 **API** | Rate limiter + retry storm, route regex 404, log sink backpressure |

**Eval set (35 entries, 3 tiers):**
- `clear` (22) — direct retrieval
- `paraphrased` (8) — query uses different words than the postmortem
- `insufficient_evidence` (5) — system should refuse to answer

---

## 🛠️ Tech Stack

| Layer | Choice | Why |
|---|---|---|
| **API** | FastAPI | Async, type-safe, auto-docs |
| **DB + Vector** | PostgreSQL + pgvector | Free, self-hosted, exact cosine at this scale |
| **Embeddings** | all-MiniLM-L6-v2 (local) | Free, no API call, upgradeable to bge-large if eval shows quality bottleneck |
| **LLM (runtime)** | Groq llama-3.1-8b-instant | Free tier, fast, provider-swappable via .env |
| **LLM (eval judge)**| GPT-4o-mini | Consistent quality for RAGAS scoring |
| **Schema** | SQLAlchemy ORM | Single source of truth (no DDL drift vs. Python models) |
| **Infrastructure** | Docker Compose | One-command setup, pinned versions |

---

## 🗺️ Phase Roadmap

| Phase | Status | What it adds |
|---|---|---|
| **0 — Data + Infrastructure** | ✅ Complete | Docker, FastAPI, 22 postmortems, eval set |
| **1 — Naive RAG** | ✅ Complete | Chunking, embedding, cosine retrieval, Groq generation |
| **2 — Advanced RAG** | 🔜 Next | BM25 hybrid, cross-encoder rerank, structure-aware chunking, RAGAS eval |
| **3 — Agentic RAG** | 📅 Planned | LangGraph router + retry loop + confidence gate + log tool |
| **3.5 — UI** | 📅 Planned | React + Vite step-trace panel via SSE streaming |
| **4 — Observability + Security**| 📅 Planned | LangSmith tracing, prompt injection defense, RBAC |

---

## ⚙️ Environment Variables

```bash
# Required
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/incident_investigator
GROQ_API_KEY=your-groq-api-key

# Optional — for RAGAS eval (Phase 2+)
OPENAI_API_KEY=your-openai-api-key

# Runtime LLM (defaults to Groq)
RUNTIME_LLM_PROVIDER=groq
RUNTIME_LLM_MODEL=llama-3.1-8b-instant

# Test DB (used by pytest only)
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/incident_investigator_test
```

---

## 🧪 Running Tests

```bash
pytest tests/test_phase1.py -v
```

**All 7 tests pass against a live test DB** (Docker must be running). The test suite uses a separate `incident_investigator_test` database — it creates and drops all tables per test function, so it never touches your real data.

---

*Phase 0 + 1 complete. See [`docs/interview_story.md`](docs/interview_story.md) for the full project narrative.*
