"""Smoke test for the policy audit pipeline (requires GROQ_API_KEY and a sample PDF)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_uploaded_policy, initialize_database, initialize_storage
from app.services.audit_pipeline import run_audit_pipeline


def _find_sample_pdf() -> Path | None:
    candidates = [
        ROOT / "data" / "kb" / "external",
        ROOT / "uploads",
    ]
    for base in candidates:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.pdf")):
            return path
    return None


def main() -> int:
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else _find_sample_pdf()
    if not pdf or not pdf.is_file():
        print("Usage: python scripts/audit_smoke_test.py [path/to/policy.pdf]")
        print("No PDF found under data/kb/external or uploads.")
        return 1

    initialize_storage()
    initialize_database()

    print(f"Auditing: {pdf.name}")
    result = run_audit_pipeline(pdf, pdf.name)
    print(json.dumps(result, indent=2, default=str))

    policy = get_uploaded_policy(result["policy_id"])
    if policy:
        sources = result.get("sources") or {}
        print(f"\nOK: policy_id={policy['policy_id']} verdict={policy['verdict_label']} sources={len(sources)}")
        return 0
    print("\nFAIL: policy not found in database")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
