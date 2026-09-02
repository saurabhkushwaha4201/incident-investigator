"""
scripts/run_eval.py — Phase 1 Retrieval Accuracy Baseline

Scoring rules by tier:
  clear / paraphrased:
    PASS  = the expected postmortem title appears in the top-5 retrieved incidents
    FAIL  = it does not

  insufficient_evidence:
    These queries are deliberately vague. The correct system behaviour is to NOT
    confidently retrieve one specific incident. We do NOT score these in the
    top-5 accuracy number — they have no defined expected doc to check against.
    Instead they are reported separately as a qualitative note:
      "system retrieved N results for a query that should ideally trigger a
       low-confidence response in Phase 3"
    Scoring them as 100% pass (because retrieval always returns rows) was a
    vacuous result — removed.

Matching is done by title substring (expected_source_doc contained in the
retrieved incident's title). This is intentionally lenient — Phase 2 will
switch to exact incident_id matching once the eval set stores database IDs.

Run from project root:
  python scripts/run_eval.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import SessionLocal
from models.db_models import Incident
from services.retrieval import retrieve_top_k


def load_eval_set():
    with open("data/eval_set.json", "r", encoding="utf-8") as f:
        return json.load(f)


def run_retrieval_eval(db, eval_data: list):
    print("==================================================")
    print("PHASE 1 BASELINE EVALUATION (Naive Vector Search)")
    print("==================================================\n")

    # Pre-fetch titles so we can map incident_id -> title
    incidents = db.query(Incident.id, Incident.title).all()
    id_to_title = {str(i.id): i.title for i in incidents}

    scored_items = [e for e in eval_data if e["expected_source_doc"] is not None]
    unscored_items = [e for e in eval_data if e["expected_source_doc"] is None]

    total = len(scored_items)
    correct_top5 = 0
    correct_top1 = 0

    tier_stats = {
        "clear":      {"total": 0, "correct_top5": 0, "correct_top1": 0},
        "paraphrased":{"total": 0, "correct_top5": 0, "correct_top1": 0},
    }

    misses = []  # collect the specific failures for post-run analysis

    for item in scored_items:
        query          = item["query"]
        expected_title = item["expected_source_doc"]
        tier           = item["difficulty"]
        eval_id        = item["id"]

        tier_stats[tier]["total"] += 1

        results = retrieve_top_k(db, query, k=5)
        retrieved_titles = [id_to_title.get(r["incident_id"], "") for r in results]

        found_in_top5 = any(expected_title in t for t in retrieved_titles)
        found_in_top1 = bool(retrieved_titles) and (expected_title in retrieved_titles[0])

        if found_in_top5:
            correct_top5 += 1
            tier_stats[tier]["correct_top5"] += 1
        else:
            misses.append({
                "id": eval_id,
                "tier": tier,
                "query": query,
                "expected": expected_title,
                "retrieved_top5": retrieved_titles,
            })

        if found_in_top1:
            correct_top1 += 1
            tier_stats[tier]["correct_top1"] += 1

    # ── Scored results ────────────────────────────────────────────────
    print(f"Scored queries (clear + paraphrased): {total}")
    print(f"  Top-5 Accuracy: {correct_top5}/{total} ({correct_top5/total*100:.1f}%)")
    print(f"  Top-1 Accuracy: {correct_top1}/{total} ({correct_top1/total*100:.1f}%)\n")

    print("Breakdown by difficulty:")
    for tier, stats in tier_stats.items():
        if stats["total"] > 0:
            top5_pct = stats["correct_top5"] / stats["total"] * 100
            top1_pct = stats["correct_top1"] / stats["total"] * 100
            print(f"  {tier.ljust(14)}: top-5 {stats['correct_top5']:2d}/{stats['total']:2d} ({top5_pct:.1f}%)  |  top-1 {stats['correct_top1']:2d}/{stats['total']:2d} ({top1_pct:.1f}%)")

    # ── Misses (the important ones) ───────────────────────────────────
    if misses:
        print(f"\nMISSES ({len(misses)} queries where correct doc not in top-5):")
        for m in misses:
            print(f"\n  [{m['id']}] ({m['tier']})")
            print(f"    Query:    {m['query']}")
            print(f"    Expected: {m['expected']}")
            print(f"    Got top-5:")
            for i, t in enumerate(m["retrieved_top5"], 1):
                print(f"      {i}. {t}")
    else:
        print("\nNo misses — all expected docs found in top-5.")

    # ── Insufficient evidence (not scored, reported qualitatively) ────
    print(f"\nInsufficient-evidence queries ({len(unscored_items)}):")
    print("  These have no expected doc — NOT included in accuracy numbers.")
    print("  Phase 3 confidence gate will be evaluated against these separately.")
    print("  (Retrieval always returns results; whether the LLM refuses to answer")
    print("   is a generation behaviour, not a retrieval metric.)")

    # ── Baseline summary ──────────────────────────────────────────────
    print("\n" + "="*50)
    print("PHASE 1 BASELINE (record these numbers before Phase 2)")
    print(f"  Top-5 Accuracy: {correct_top5}/{total} ({correct_top5/total*100:.1f}%)")
    print(f"  Top-1 Accuracy: {correct_top1}/{total} ({correct_top1/total*100:.1f}%)")
    print("  Insufficient-evidence: NOT SCORED (no retrieval metric defined for Phase 1)")
    print("="*50)


if __name__ == "__main__":
    db = SessionLocal()
    try:
        run_retrieval_eval(db, load_eval_set())
    finally:
        db.close()
