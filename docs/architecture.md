# Tollgate — Fictional Org Architecture Reference

> **Internal reference only.** Every postmortem written for this project must use
> exactly the service names and architectural facts documented here. Inconsistency
> (e.g., `billing-service` vs `billing_service`, or "rate limiter lives in Redis"
> vs "rate limiter lives in api_gateway") breaks the log generation logic and
> quietly corrupts the eval corpus.

---

## Canonical Service Names

These are the **only** strings that may appear in `service_tags`, in log `service`
fields, and in the `_LOG_MESSAGE_TEMPLATES` dict in `services/ingestion.py`.

| Service (exact string) | Role | Log templates in ingestion.py? |
|---|---|---|
| `api_gateway` | Ingress, rate limiting (Redis-backed), request routing | ✅ Full set |
| `billing_service` | Stripe webhook processing, idempotency checks, usage metering | ✅ Full set |
| `auth_service` | JWT issuance, refresh token rotation, session management | ✅ Full set |
| `redis` | Shared in-memory store; used by api_gateway (rate limiter) and billing_service (idempotency) | ✅ Full set |
| `user_service` | User profile CRUD, account status management | ⚠️ Falls back to api_gateway templates |
| `postgres` | Primary relational DB; connection pool managed via SQLAlchemy | ⚠️ Falls back to api_gateway templates |

> **Adding a new service?** Add its key to `_LOG_MESSAGE_TEMPLATES` in
> `services/ingestion.py` at the same time as updating this file. The fallback
> to `api_gateway` is silent — a typo in service_tags produces wrong log messages,
> not an error.

---

## Architecture Constraints

Every postmortem must be technically coherent with these facts. If a postmortem
contradicts them, fix the postmortem — not the architecture.

### Rate Limiting
- Lives in **`api_gateway`**, not in individual services
- Backed by **`redis`** — stores per-IP/per-user counters with a sliding window TTL
- Failure mode: if Redis is unreachable, the rate limiter has a circuit breaker
  that can be configured fail-open (passes all traffic) or fail-closed (blocks all)
- Default config: **fail-open** (chosen to preserve availability; the source of
  the double-charge incident class)

### Webhook Idempotency
- Lives in **`billing_service`**
- Backed by **`redis`** — idempotency key stored for **24 hours** after first processing
- Flow: `billing_service` receives Stripe webhook → looks up idempotency key in Redis
  → if key exists, discard (already processed); if not, process and write key
- Failure mode: if Redis is down, the key lookup returns a miss → duplicate processing

### Authentication
- **`auth_service`** issues JWT access tokens with a **15-minute TTL**
- Refresh tokens are stored in **`redis`** with a longer TTL and are single-use
  (rotation: old token invalidated on use, new one issued)
- Replay attack surface: if the old refresh token is not invalidated before the
  new one is issued (race or bug), an attacker who captured the old token can
  reuse it → triggers a session invalidation storm when detected

### Database
- **`postgres`** is the primary relational DB
- Connection pool bounded at **10 connections** (SQLAlchemy default)
- Pool exhaustion cascades: once all 10 connections are taken, new requests queue
  or timeout — multiple services sharing the pool can block each other
- **`api_gateway` does NOT connect to Postgres directly** — it proxies to downstream
  services; all Postgres access goes through `billing_service`, `auth_service`,
  or `user_service`

### Service Dependencies (what calls what)
```
External traffic
      │
      ▼
 api_gateway ──► auth_service ──► postgres (user sessions)
      │                │
      │                └──► redis (refresh tokens)
      │
      ├──► billing_service ──► postgres (usage records)
      │           │
      │           ├──► redis (idempotency keys)
      │           └──► stripe.com (outbound webhook delivery)
      │
      └──► user_service ──► postgres (user profiles)
                │
                └──► postgres read replica (for reads — lag possible)

stripe.com ──► api_gateway ──► billing_service (inbound webhooks)
```

### What Lives Where (common postmortem detail sources)
| Feature | Component | Failure mode |
|---|---|---|
| Per-IP rate limiting | `api_gateway` + `redis` | Redis down → fail-open → traffic spike |
| Webhook deduplication | `billing_service` + `redis` | Redis down → duplicate processing |
| Refresh token rotation | `auth_service` + `redis` | Redis down or bug → token replay |
| Connection pool | `postgres` (shared) | High concurrency → pool exhaustion → cascading queues |
| Outbound Stripe calls | `billing_service` → stripe.com | TLS expiry, timeout, 5xx from Stripe |
| Inbound Stripe webhooks | stripe.com → `api_gateway` → `billing_service` | HMAC mismatch, body parsing error |

---

## Severity Definitions

| Level | Criteria |
|---|---|
| **SEV1** | Direct customer financial impact (double-charge, data loss) or complete service unavailability (>1k users) |
| **SEV2** | Degraded service (partial outage, elevated error rates), no direct financial impact, <1k users affected |
| **SEV3** | Minor degradation, caught quickly (<5 min), no significant customer impact |

---

## Timeline Conventions

Postmortem timelines must follow this realistic shape:
1. **Detection** — alert fires or customer report received
2. **Initial diagnosis** — first hypothesis formed
3. **Confirmation** — root cause confirmed (not just suspected)
4. **Mitigation** — incident rate stops rising (e.g., Redis restarted, config rolled back)
5. **Resolution** — full recovery confirmed, monitoring normalized

Timestamp spacing: events should be **2–15 minutes apart**. A timeline with
events 30 seconds apart or 2 hours apart is unrealistic for a human-investigated incident.

---

*Last updated: 2026-08-31. If this file and a postmortem conflict, update the postmortem.*
