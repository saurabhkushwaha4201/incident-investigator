# Phase 0 — Setup & Data Prep: Deep Dive

> This document covers *what* we built in Phase 0, *why* each decision was made,
> and *how* the pieces fit together. Read this before talking about the project in
> an interview — every section has a "Why it matters" callout you should be able
> to explain unprompted.

---

## What Phase 0 Delivers

By the end of Phase 0 the system has:

| Artifact | Status |
|---|---|
| Postgres + pgvector running in Docker | ✅ Done |
| FastAPI app boots cleanly, tables auto-created | ✅ Done |
| `POST /incidents` endpoint — atomically ingests postmortem + logs | ✅ Done |
| `POST /query` endpoint — naive RAG response | ✅ Done |
| 22 postmortems — final after corpus audit (see §3 for why 3 were cut) | ✅ Done |
| 35-entry eval set (3 tiers) | ✅ Done |
| Seed script — calls `create_incident()` directly, no HTTP dependency | ✅ Done |

---

## 1. Infrastructure

### What we built
- **`docker-compose.yml`** — spins up a single `pgvector/pgvector:pg15` container, mounts a named volume (`pgdata`) so data survives `docker-compose restart`.

- **`db/init.sql`** — runs once at container creation: `CREATE EXTENSION IF NOT EXISTS vector`. That's its entire job.
- **`db/database.py`** — SQLAlchemy engine + `SessionLocal` factory + `get_db()` FastAPI dependency. Named `database.py`, not `session.py`, to avoid confusion with *auth* sessions (JWT, cookie sessions) which appear in Phase 4. A reviewer skimming the repo should not conflate database sessions with user sessions.

### Why this specific split
The `init.sql` file only runs on first container initialization. If you put your `CREATE TABLE` statements there too, they'd conflict with SQLAlchemy's `create_all()` on the second run. The rule: **`init.sql` owns extensions; SQLAlchemy models own schema**.

> **Interview question:** *Why use Docker instead of a local Postgres install?*
> Reproducible in one command (`docker-compose up -d`), pins exact Postgres + pgvector versions together. Any reviewer can clone the repo and be running in under 3 minutes.

> **Interview question:** Why choose PG15 over PG17?
> And  the correct senior-level answer is: "For a database, I prioritize stability and extension compatibility over the newest version, unless there is a specific PG17 feature we need. For this project, PG15 does exactly what we need flawlessly."

---

## 2. Fictional Org Architecture (Tollgate)

### What we built
`docs/architecture.md` — the single source of truth for every service name, failure mode, and architectural constraint used across all 22 postmortems.

### The six canonical services
| Service | Role |
|---|---|
| `api_gateway` | Ingress, rate limiting (Redis-backed), routing |
| `billing_service` | Stripe webhooks, idempotency, usage metering |
| `auth_service` | JWT issuance, refresh token rotation |
| `redis` | Rate-limit counters + idempotency key store |
| `user_service` | User CRUD, reads from Postgres read replica |
| `postgres` | Primary relational DB, 10-connection pool |

### Why internal consistency matters
The log generation code in `services/ingestion.py` uses `_LOG_MESSAGE_TEMPLATES` keyed by exact service names. If a postmortem says `billing-service` (hyphen) instead of `billing_service` (underscore), the log generator silently falls back to `api_gateway` templates — producing completely irrelevant logs for a billing incident. The architecture doc is the contract that prevents this.

> **Interview question:** *Why a fictional org instead of real public postmortems?*
> Three reasons: (1) Real postmortems describe AWS's/Cloudflare's internal architecture — you can't generate matching logs because you don't know their real service topology. (2) Verbatim public content is a copyright risk. (3) We need to own the ground-truth root cause sections for the eval set. We drew on real failure *patterns* (Redis fail-open, thundering herd, token replay) but rewrote them into Tollgate's context.

---

## 3. The Postmortem Corpus (22 incidents)

### Exact headers every postmortem follows
(Defined in `docs/postmortem_template.md`)

```
## Timeline
## Root Cause
## Resolution
## Related Services/Errors
(+ Summary, Impact, Detection, Action Items, Lessons Learned)
```

> **Why exact headers matter:** Phase 2's structure-aware chunker splits documents at these header boundaries. `## Root Cause Analysis` won't match the split pattern — that chunk gets mis-labeled. The header strings are a compile-time contract.

### Two tiers
- **Tier 1 (7 hand-authored):** Incidents you can explain unprompted without notes: Redis fail-open double-charge, HMAC mismatch webhook drop, refresh token replay, idempotent upsert race, circuit breaker flapping, Postgres pool exhaustion, rate limiter + retry storm.
- **Tier 2 (15 adapted):** Real failure patterns (thundering herd, DNS TTL, TLS cert expiry, thread leak OOM, etc.) rewritten into Tollgate's context with Tollgate's service names.

### Corpus audit and why 3 files were deleted
During eval set construction we discovered 3 files that were contaminated and deleted:

1. **`clear-billing-charging-twice`** — had `Status: Draft`, `Root Cause: TBD`, a single-line timeline, and its title literally started with `"Clear:"` — the eval difficulty tier label leaking into the document title. It was replaced with a fully-rewritten `redis-fail-open-double-charge.md` with Final status, confirmed root cause, and 9-event timeline.

2. **`paraphrased-payment-duplicate`** and **`insufficient-evidence-errors-in-prod`** — their titles started with `"Paraphrased:"` and `"Insufficient Evidence:"` respectively. These tier labels should only appear in the eval set's `difficulty` field, never in a postmortem title. A retrieval system trained on these would learn to associate the difficulty tier with billing topics — a data contamination that would silently inflate eval scores for those tiers.

**How these were caught:** An automated quality audit script checked all `.md` files for: required headers present, `Status != Draft`, no `TBD` in body, ≥3 timeline entries, and title strings not starting with eval tier names. 0 issues across 22 final files.

### The `.json` sidecar files
```json
{
  "title": "Billing double-charge — Redis fail-open",
  "service_tags": ["api_gateway", "redis", "billing_service"],
  "timeline": [
    {"time": "2026-07-15T14:30:00Z", "event": "Redis connection pool exhausted"},
    ...
  ]
}
```
`postmortem_body` is loaded from the `.md` at seed time — never duplicated in JSON (no copy/paste drift).

### Seed script idempotency
**Running `seed_incidents.py` twice duplicates data.** There is no title-uniqueness check in `create_incident()`. A second run produces 44 incidents with identical content. This is intentional: the seed script is a setup utility, not a migration tool. Before re-running, wipe the DB:
```bash
docker exec <container> psql -U postgres -d incident_investigator -c "TRUNCATE incidents CASCADE;"
```
`TRUNCATE ... CASCADE` also clears `chunks` and `logs` (both have FK ON DELETE CASCADE).

---

## 4. The Ingestion Endpoint (`POST /incidents`)

### What happens on every call
```
POST /incidents
    │
    ├─ 1. INSERT into incidents table → get back UUID
    ├─ 2. chunk_text(postmortem_body) → embed_texts(chunks) → INSERT chunks
    └─ 3. For each timeline event: 20-80 ERROR/WARN logs ±30s + ~10% INFO noise ±300s
           └─ All three committed atomically (one transaction)
```

### Why one endpoint for everything
The PRD states this explicitly: *"Ingestion is continuous, not a one-time batch load."* The seed script (`scripts/seed_incidents.py`) calls `create_incident()` directly from `services/ingestion.py` — not over HTTP. This means Phase 3.5's live-demo "add a new incident" UI uses the same endpoint code — zero extra code.

### The seed script evolution — why it calls `create_incident()` directly
The first version of `seed_incidents.py` called `POST /incidents` over HTTP with a 60-second timeout. A postmortem with 9 timeline events generates ~720 log rows during seeding — embedding + DB writes exceeded 60 seconds on CPU, causing a `ReadTimeoutError`.

The fix: call `create_incident()` directly, bypassing HTTP entirely:

```
Old: Python script → HTTP → uvicorn → create_incident()
New: Python script → create_incident() directly
```

This is better design regardless of the timeout issue. A seed script is a dev/ops utility, not a user-facing request. It should not depend on a running web server.

### Atomicity guarantee
`db.flush()` assigns `incident.id` (FK needed for chunks + logs), then chunks and logs are inserted, then `db.commit()`. If embedding fails mid-way, nothing is committed — no orphaned incidents without chunks, no orphaned chunks without an incident.

### Synthetic log design
- **Correlated:** clustered ±30 seconds around each timeline event's timestamp
- **Noisy:** ~10% background INFO rows at ±300 seconds so Phase 2's log search can't trivially filter by `ERROR` level alone
- **Realistic:** service-specific messages (Redis timeouts for Redis events, not generic 500s)

---

## 5. The Eval Set (35 entries)

### Three tiers

| Tier | Count | What it tests |
|---|---|---|
| `clear` | 22 | Direct retrieval — query matches the incident topic |
| `paraphrased` | 8 | Semantic retrieval — query uses different words ("charged twice" vs "duplicate webhook processing") |
| `insufficient_evidence` | 5 | Phase 3 confidence gate — query is under-specified, correct answer is "I don't know" |

### Why paraphrased cases matter
They are the entire point of using an embedding model instead of keyword search. If the system only works when the query uses the same words as the postmortem, you've built a more expensive grep. Paraphrased cases are what separate "I called an embedding API" from "I understand semantic retrieval."

### Why insufficient_evidence cases matter
They test what separates a naive RAG from a production one. A system that always confidently answers is dangerous in incident response. Phase 3's confidence gate is specifically measured against these 5 entries.

---

## 6. PRD Compliance Check

| PRD Requirement | Our Implementation | Status |
|---|---|---|
| Repo scaffold: FastAPI, Postgres + pgvector via Docker | docker-compose.yml, db/, main.py | ✅ |
| 20–25 postmortems with consistent markdown template | 22 postmortems in data/postmortems/ (3 contaminated stubs deleted) | ✅ |
| 30–40 question eval set, 3 tiers | 35 entries in data/eval_set.json | ✅ |
| Single `POST /incidents` endpoint (not batch folder walk) | routers/incidents.py + services/ingestion.py | ✅ |
| Atomic ingestion (postmortem + logs together) | db.flush() + db.commit() in create_incident() | ✅ |
| Logs NOT embedded (SQL-queried only) | LogEntry table, no embedding column | ✅ |
| `POST /query` endpoint working | routers/query.py | ✅ |
| Eval set expected_source_doc matches actual postmortem titles exactly | Verified by automated audit script | ✅ |
| Eval set uses different wording than postmortems | Paraphrased tier: 8/8 top-5 PASS confirmed | ✅ |
| All postmortems Final status, confirmed root cause, ≥3 timeline entries | Automated audit: 0 issues across 22 files | ✅ |

---

## 7. Common Interview Questions

**Q: Why pgvector instead of Pinecone/Qdrant?**
> Pinecone costs money, requires a network call, and adds an external dependency. pgvector runs in the same Postgres instance you already have — free, no API quota, no latency overhead. At 200 chunks, you don't need Pinecone's indexing features.

**Q: Why not embed the logs?**
> Logs are structured — service name, timestamp, status code, log level. These are best queried with exact SQL filters (WHERE service = 'redis' AND timestamp BETWEEN ... AND level = 'ERROR'). Embedding them for fuzzy semantic search is almost never what you want during incident response. Correcting this mistake is itself a signal of understanding when *not* to use vector search.

**Q: How does the eval set avoid being circular?**
> Eval queries deliberately use different words than the source postmortems. "Customers were charged twice" vs the postmortem's "duplicate webhook processing" — the system has to find the connection semantically, not by string matching.

**Q: Why UUID primary keys instead of integer IDs?**
> UUIDs are safe to generate client-side (no need to round-trip to the DB for the PK before inserting FK-dependent rows), have no ordering information that could leak insertion sequence, and are the industry standard for distributed systems. At this scale it makes no practical difference, but it's the right default.
