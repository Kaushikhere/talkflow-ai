"""Quick test for policy comparison."""
from __future__ import annotations

import json
import sys

from app.database import get_uploaded_policy
from app.services.audit_compare import (
    _COMPARE_SYSTEM_PROMPT,
    _comparison_payload,
    compare_policies,
)
from app.config import AUDIT_ANALYSIS_MODEL, AUDIT_MAX_TOKENS_COMPARE
from app.services.groq_client import get_groq_client


def main() -> None:
    policy_a_id = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    policy_b_id = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    row_a = get_uploaded_policy(policy_a_id)
    row_b = get_uploaded_policy(policy_b_id)
    if not row_a or not row_b:
        print("Policy not found")
        return

    try:
        result = compare_policies(row_a, row_b)
        print("OK winner=", result["winner"])
        return
    except Exception as exc:
        print("compare_policies error:", exc)

    payload_a = _comparison_payload(row_a)
    payload_b = _comparison_payload(row_b)
    user_prompt = (
        "Compare Policy A and Policy B using ONLY the JSON below.\n"
        "Pick exactly one WINNER (A or B) for a typical consumer seeking "
        "strong hospitalization coverage.\n"
        "Write ELIMINATION JUSTIFICATION explaining why the other policy loses.\n\n"
        "Reply with ONLY valid JSON (no markdown):\n"
        '{"winner": "A|B", "elimination_justification": "2-4 blunt sentences."}\n\n'
        f"Policy A:\n{json.dumps(payload_a, indent=2)}\n\n"
        f"Policy B:\n{json.dumps(payload_b, indent=2)}\n"
    )
    client = get_groq_client()
    response = client.chat.completions.create(
        model=AUDIT_ANALYSIS_MODEL,
        messages=[
            {"role": "system", "content": _COMPARE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=AUDIT_MAX_TOKENS_COMPARE,
    )
    raw = response.choices[0].message.content or ""
    with open("compare_debug.txt", "w", encoding="utf-8") as f:
        f.write(f"content len={len(raw)}\n")
        f.write(raw)
        if hasattr(response.choices[0].message, "model_dump"):
            f.write("\n\nDUMP:\n")
            f.write(str(response.choices[0].message.model_dump()))
    print("Wrote compare_debug.txt, len=", len(raw))


if __name__ == "__main__":
    main()
