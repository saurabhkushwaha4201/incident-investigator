# Postmortem Template

> **CRITICAL:** The section headers below (`## Timeline`, `## Root Cause`,
> `## Resolution`, `## Related Services/Errors`) are **exact strings**.
> Phase 2's structure-aware chunker splits on these exact strings.
> Any variation (`## Root Cause Analysis`, `## Root-Cause`) creates an
> unrecognised chunk boundary and will silently mis-chunk the document.

---

```markdown
# Incident: <short title, e.g. "Billing double-charge — Redis fail-open">

**Date:** YYYY-MM-DD
**Service(s) affected:** <comma-separated exact names from docs/architecture.md>
**Severity:** <SEV1 | SEV2 | SEV3>

## Timeline
- HH:MM UTC — <event: what happened, not what was suspected>
- HH:MM UTC — <event>
- HH:MM UTC — <event>
(4-8 entries, 2-15 min apart: detection → diagnosis → mitigation → resolution)

## Root Cause
<One precise paragraph. This is the eval set's ground truth.
Write it as confirmed fact — not "we believe" or "probably" — as if
written after the investigation is complete and the cause is known.
Name the service, the failure mode, and the propagation path explicitly.
Example: "Redis became unreachable, causing api_gateway's rate limiter to
fail open. billing_service's idempotency check also relied on Redis, so when
a Stripe webhook retry arrived during the outage, the key lookup returned a
miss and the webhook was processed twice, charging the customer a second time.">

## Resolution
<One paragraph: what stopped the incident and what prevents recurrence.
Include both the immediate fix (e.g. "Redis was restarted") and the
structural change (e.g. "Added database-level idempotency fallback").>

## Related Services/Errors
<Comma-separated tags that drive log template selection in services/ingestion.py.
Include: relevant service names, error types, HTTP status codes, key terms.
Example: "redis timeout, rate_limiter fail-open, billing_service idempotency, 503, duplicate charge">
```

---

## Checklist Before Saving a Postmortem

- [ ] Service names in `**Service(s) affected:**` match `docs/architecture.md` exactly
- [ ] `service_tags` in the matching `.json` file also match architecture.md
- [ ] Timeline has 4-8 events, 2-15 min apart
- [ ] Timeline follows: detection → diagnosis → confirmation → mitigation → resolution
- [ ] Root Cause paragraph is specific enough to be ground truth (names service + failure mode + propagation)
- [ ] No verbatim text from any public postmortem
- [ ] Technically coherent with architecture constraints in `docs/architecture.md`
- [ ] You can explain the root cause unprompted without re-reading the doc
