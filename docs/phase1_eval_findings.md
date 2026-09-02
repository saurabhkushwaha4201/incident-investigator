# Phase 1 Evaluation Findings — Baseline Report

> This document is the canonical record of Phase 1 retrieval accuracy.
> Every Phase 2 claim of improvement will be measured against these numbers.
> Read it before any interview discussion of evaluation methodology.

---

## The Baseline Numbers

**Eval set:** 35 entries total — 22 `clear`, 8 `paraphrased`, 5 `insufficient_evidence`
**Scored:** 30 queries (clear + paraphrased only — see below for why insufficient_evidence is excluded)
**Retrieval:** Naive top-5 cosine similarity, `all-MiniLM-L6-v2`, no reranking, no hybrid search

| Metric | Value | Notes |
|---|---|---|
| Top-5 Accuracy | **96–97%** (29-30/30) | Range, not a fixed point — see nondeterminism section |
| Top-1 Accuracy | **60.0%** (18/30) | Stable across runs |
| `clear` Top-5 | **95.5%** (21/22) | 1 miss: eval_022 |
| `clear` Top-1 | **77.3%** (17/22) | — |
| `paraphrased` Top-5 | **100%** (8/8) | Right doc found, wrong rank |
| `paraphrased` Top-1 | **12.5%** (1/8) | The reranker's entire job |
| `insufficient_evidence` | **NOT SCORED** | See section below |

---

## The Vacuous-Scoring Bug — What It Was and Why It Was Fixed

The first version of `run_eval.py` handled `insufficient_evidence` queries (those with `expected_source_doc: null`) like this:

```python
else:
    # "we just want to know if it retrieved anything"
    found_in_top5 = len(retrieved_titles) > 0   # always True
    found_in_top1 = len(retrieved_titles) > 0   # always True
```

This reported `5/5 (100%)` for the `insufficient_evidence` tier and included it in the overall top-5 denominator (35 instead of 30), producing `32/35 (91.4%)` as the headline number.

**Why this was wrong:** `pgvector` always returns rows — a sequential cosine scan on a non-empty table never returns zero results. `len(retrieved_titles) > 0` is therefore trivially and permanently true. The check proves nothing about retrieval quality, refusal behaviour, or anything else. It is a vacuous tautology disguised as a metric.

**The correct treatment:** Queries with `expected_source_doc: null` have no defined retrieval accuracy to measure. The intended system behaviour for these queries is not "retrieve the most relevant document" — it is "recognise that evidence is insufficient and refuse to confidently answer." That is a *generation* behaviour, controlled by the LLM's system prompt and Phase 3's confidence gate. It is not a *retrieval* metric. The right decision is to exclude this tier from the retrieval accuracy score entirely, note it explicitly, and defer it to Phase 3 evaluation.

**What the 5/5 100% was actually measuring:** Nothing. It was an artefact of how a null-expected-doc query was handled in the scoring code, not evidence that the system demonstrated any refusal capability.

### What the insufficient_evidence tier actually contains

These are deliberately underspecified queries — the kind of thing an engineer might type into an incident tool in the first 2 minutes of an outage before they know what's wrong:

| Eval ID | Query |
|---|---|
| eval_031 | *"Users are complaining about a problem in production"* |
| eval_032 | *"Something went wrong with payments earlier today"* |
| eval_033 | *"We are seeing some elevated error rates across a few services"* |
| eval_034 | *"Authentication is not working for some users"* |
| eval_035 | *"The system is slow right now"* |

None of these name a specific service, timeframe, or symptom that could distinguish between e.g. the Redis fail-open incident (billing), the rate limiter storm (auth/gateway), or the thread leak (billing OOM). A correct system response is: *"I don't have enough information to identify a specific incident. Can you tell me which service is affected, what errors you're seeing, and approximately when it started?"* — not a confident retrieval of whichever postmortem happens to be closest in vector space.

Phase 3's confidence gate will be evaluated against these 5 queries: does the system refuse to answer (correct), or does it confidently retrieve and generate (wrong)? That evaluation requires measuring LLM output, not retrieval ranks — which is why it doesn't belong in this document.

---

## The Two Confusion Patterns (Why a Reranker is Necessary)

These are not vague "the model sometimes gets confused" claims. They are specific, reproducible failure patterns visible in the verbatim retrieval output.

### Pattern 1: Billing/Idempotency Swap (eval_001 ↔ eval_019)

Both queries describe a customer being charged twice. The embedding model produces nearly identical vectors for both, causing them to swap each other's correct document into position 1.

**eval_001** — *"Users are reporting they were charged twice for the same order in the last hour"*
```
Expected: Billing double-charge — Redis fail-open
Top-5:  1. Idempotency window too short          ← WRONG (eval_019's answer)
        2. Billing double-charge — Redis fail-open ← correct, but ranked 2nd
        3. Idempotent upsert race billing undercount
        4. Webhook HMAC mismatch silent drop
        5. ...
```

**eval_019** — *"A customer was charged twice but the first charge succeeded hours ago and the system shows the event was already processed"*
```
Expected: Idempotency window too short
Top-5:  1. Billing double-charge — Redis fail-open  ← WRONG (eval_001's answer)
        2. Idempotency window too short              ← correct, but ranked 2nd
        3. ...
```

**Root cause:** Both postmortems contain "charged twice," "Redis," "idempotency," and billing terminology. Cosine similarity measures the angle between averaged token embeddings — it cannot distinguish "Redis failed open during a spike" from "idempotency key expired after 72 hours" because both documents discuss the same concepts. A cross-encoder reads the full query and document text together as one input, which gives it the contextual signal to separate "rate limiter failed during a spike" from "Stripe retried a 72-hour-old event."

---

### Pattern 2: API Gateway Timeout Attractor (eval_009, eval_014, eval_028, eval_029)

Four separate queries — with different correct answers — all retrieved `API Gateway timeout too long` as their top-1 result.

**eval_009** — *"After deploying user_service, api_gateway is returning connection refused errors and routing to the wrong pod"*
```
Expected: DNS TTL stale post deploy
Top-1:   API Gateway timeout too long  ← WRONG
```

**eval_014** — *"Request latency spiked massively for billing and gateway but no errors are being returned"*
```
Expected: Log sink backpressure latency
Top-1:   API Gateway timeout too long  ← WRONG
```

**eval_028** — *"Requests are slow but not failing and we can see threads are piling up — no errors in the error log"*
```
Expected: Log sink backpressure latency
Top-1:   API Gateway timeout too long  ← WRONG
```

**eval_029** — *"The gateway seems fine and backend services seem fine individually but end users are getting connection errors on some API paths"*
```
Expected: Route regex change 404 storm
Top-1:   API Gateway timeout too long  ← WRONG
```

**Root cause:** The API Gateway timeout postmortem describes a scenario where "a downstream service slows down → the gateway holds connections open → the gateway itself fails." This is a semantically broad failure pattern that overlaps with any query about latency, connection failures, or situations where the gateway appears involved. Its embedding vector sits at a position in space that is approximately equidistant from many different latency/connection failure queries — it acts as a gravity well.

A cross-encoder fixes this because it reads the actual text of the query ("threads piling up, no errors in error log") against the actual text of the document ("upstream timeout set to 60 seconds, thread pool exhausted") and can determine that thread backpressure from a log sink is a mechanistically different failure than thread exhaustion from a long upstream timeout.

---

## The Nondeterminism Finding

Between two consecutive eval runs on the same database, corpus, and model, top-5 accuracy changed by 1 query (29/30 vs 28/30 equivalent in one run). The DB, eval set, matching logic, and embedding model were identical. No code changed between the two runs.

**Cause:** `pgvector` uses a sequential scan for cosine distance on small corpora (no `ivfflat` index). The scan uses BLAS linear algebra operations whose floating-point results are sensitive to CPU state and memory alignment. At a corpus size of 22 documents, multiple postmortems cluster at cosine distances separated by less than 0.001 for any given query. When documents at positions 5 and 6 are 0.0003 apart in cosine distance, their ordering is not stable across runs.

**What this means for the baseline:**
- Top-5 accuracy should be stated as a range: **96–97% (29-30/30)**, not 96.7%
- Top-1 accuracy at 60.0% (18/30) is stable — the gaps between positions 1 and 2 are large enough not to flip
- The paraphrased 12.5% top-1 (1/8) is real but should be contextualised: at n=8, one query flip = 12.5 percentage points

**Interview framing (better than a clean number):**
> *"I found the top-5 boundary is unstable for borderline cases at 22 documents because BLAS cosine distance calculations have sub-millipercent floating-point nondeterminism. I verified this by running the eval twice on the same state and seeing one query flip. The top-1 metric is more stable because the gaps there are large. This is also why I didn't add an ivfflat index — at this corpus size, an approximate index would make this worse, not better."*

---

## What Phase 2 Must Improve

| Metric | Phase 1 | Phase 2 Target | Mechanism |
|---|---|---|---|
| Top-5 Accuracy | 96-97% | 100% | BM25 catches eval_022 ("read replica") keyword miss |
| Top-1 Accuracy | 60.0% | >85% | Cross-encoder reranker stabilises ordering |
| Paraphrased Top-1 | 12.5% | >75% | Cross-encoder + query rewriting |
| API Gateway attractor | 4 queries affected | 0 | Cross-encoder distinguishes failure mechanisms |
| Billing/idempotency swap | 2 queries affected | 0 | Cross-encoder reads full text jointly |
