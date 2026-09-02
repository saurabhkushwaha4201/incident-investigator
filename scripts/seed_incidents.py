"""
scripts/seed_incidents.py -- Load all postmortems into the Incident Investigator.

Calls create_incident() directly (bypasses HTTP) so there is no 60s timeout
risk from embedding + log generation on CPU.

Pre-flight checks:
  1. Docker must be running (database needs to be up):
       docker-compose up -d
  2. Run from the project root:
       python scripts/seed_incidents.py

After seeding, verify in psql:
  SELECT count(*) FROM incidents;   -- should be 20-25
  SELECT count(*) FROM chunks;      -- should be > 0
  SELECT count(*) FROM logs;        -- should be > 0
  SELECT service, count(*) FROM logs GROUP BY service;
"""
import glob
import json
import os
import sys
from pathlib import Path

# Add project root to path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import SessionLocal
from services.ingestion import create_incident

POSTMORTEMS_DIR = "data/postmortems"


def seed():
    json_files = sorted(glob.glob(f"{POSTMORTEMS_DIR}/*.json"))
    if not json_files:
        print(f"No JSON files found in {POSTMORTEMS_DIR}/. Exiting.")
        return

    print(f"Found {len(json_files)} postmortem(s) to seed.\n")
    success_count = 0
    failed_count = 0

    db = SessionLocal()
    try:
        for filepath in json_files:
            slug = Path(filepath).stem

            # Load JSON sidecar
            with open(filepath, encoding="utf-8") as f:
                payload = json.load(f)

            # Load postmortem_body from the matching .md file if not inline in JSON.
            if not payload.get("postmortem_body"):
                md_path = filepath.replace(".json", ".md")
                if not os.path.exists(md_path):
                    print(f"  [FAIL] [{slug}]: missing postmortem_body and no matching .md file")
                    failed_count += 1
                    continue
                with open(md_path, encoding="utf-8") as f:
                    payload["postmortem_body"] = f.read()

            try:
                incident = create_incident(
                    db=db,
                    title=payload["title"],
                    postmortem_body=payload["postmortem_body"],
                    service_tags=payload.get("service_tags", []),
                    timeline=payload.get("timeline", []),
                )
                print(f"  [OK] [{slug}] -> incident_id: {incident.id}")
                success_count += 1
            except Exception as e:
                print(f"  [FAIL] [{slug}]: {e}")
                db.rollback()
                failed_count += 1

    finally:
        db.close()

    print(f"\n{'='*50}")
    print(f"Seeding complete: {success_count} indexed, {failed_count} failed.")

    if failed_count > 0:
        print("Fix failures above before running the eval set.")
    else:
        print("\nNext: run scripts/run_eval.py to get Phase 1 baseline scores.")


if __name__ == "__main__":
    seed()
