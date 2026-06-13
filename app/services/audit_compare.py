"""Cross-policy comparison via Groq analysis."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import AUDIT_ANALYSIS_MODEL, AUDIT_MAX_TOKENS_COMPARE
from app.services.audit_pipeline import _parse_json_response, policy_to_response
from app.services.groq_client import get_groq_client, groq_assistant_text

logger = logging.getLogger(__name__)

_COMPARE_SYSTEM_PROMPT = (
    "You are an expert, blunt, independent health insurance auditor. "
    "Compare two policy scorecards and declare a single WINNER based on "
    "consumer financial protection: lower out-of-pocket risk, fewer caps, "
    "shorter waiting periods, and stronger restoration benefits. "
    "Do not invent facts beyond the supplied metrics. "
    "Respond with ONLY compact JSON, no markdown fences or extra prose."
)


def _groq_assistant_text(message: Any) -> str:
    """Return visible content; gpt-oss models may leave content empty and use reasoning."""
    return groq_assistant_text(message)


def _normalize_compare_data(data: dict[str, Any]) -> dict[str, Any]:
    winner = str(data.get("winner") or "").upper().strip()
    if winner in {"POLICY_A", "A"}:
        winner = "A"
    elif winner in {"POLICY_B", "B"}:
        winner = "B"
    else:
        winner = "A"

    justification = str(
        data.get("elimination_justification") or data.get("justification") or ""
    ).strip()
    if not justification:
        justification = "No elimination justification was generated."

    return {"winner": winner, "elimination_justification": justification}


def _infer_winner_from_text(text: str) -> str | None:
    winner_match = re.search(r'"winner"\s*:\s*"([AB])"', text, re.I)
    if winner_match:
        return winner_match.group(1).upper()

    winner_match = re.search(r"\bWINNER:\s*(A|B)\b", text, re.I)
    if winner_match:
        return winner_match.group(1).upper()

    if re.search(r"\b(?:thus|therefore|so)\s+B\s+wins\b", text, re.I):
        return "B"
    if re.search(r"\b(?:thus|therefore|so)\s+A\s+wins\b", text, re.I):
        return "A"
    if re.search(r"\bB\s+(?:wins|is the winner|offers better)\b", text, re.I):
        return "B"
    if re.search(r"\bA\s+(?:wins|is the winner|offers better)\b", text, re.I):
        return "A"
    return None


def _extract_justification(text: str) -> str:
    justification_match = re.search(
        r'"elimination_justification"\s*:\s*"((?:\\.|[^"\\])*)',
        text,
        re.I | re.S,
    )
    if justification_match:
        return justification_match.group(1).strip().replace('\\"', '"')

    justification_match = re.search(
        r"ELIMINATION JUSTIFICATION:\s*(.+?)(?=WINNER:|$)",
        text,
        re.I | re.S,
    )
    if justification_match:
        return justification_match.group(1).strip()

    sentences = re.findall(r"[A-Z][^.!?]*[.!?]", text)
    for sentence in reversed(sentences):
        lower = sentence.lower()
        if "policy a" in lower or "policy b" in lower or "co-pay" in lower or "waiting" in lower:
            return sentence.strip()
    return ""


def _parse_compare_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        raise ValueError("Comparison model returned empty response.")

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()

    try:
        return _normalize_compare_data(_parse_json_response(text))
    except json.JSONDecodeError:
        pass

    winner = _infer_winner_from_text(text)
    if winner:
        justification = _extract_justification(text)
        if not justification:
            justification = (
                f"Policy {winner} provides stronger financial protection based on the supplied metrics."
            )
        return {"winner": winner, "elimination_justification": justification}

    raise ValueError("Comparison model returned unparseable response.")


def _comparison_payload(policy: dict) -> dict[str, Any]:
    """Build a compact metrics object from a stored policy row."""
    response = policy_to_response(policy)
    metrics = response.get("metrics") or {}
    return {
        "policy_id": policy["policy_id"],
        "filename": policy.get("filename"),
        "verdict_label": policy.get("verdict_label"),
        "metrics": metrics,
        "recommendation_summary": response.get("recommendation_summary"),
        "key_risks": response.get("key_risks") or [],
        "key_strengths": response.get("key_strengths") or [],
    }


def compare_policies(policy_a: dict, policy_b: dict) -> dict[str, Any]:
    """Run Groq comparison and return winner + both scorecards."""
    payload_a = _comparison_payload(policy_a)
    payload_b = _comparison_payload(policy_b)

    user_prompt = (
        "Compare Policy A and Policy B using ONLY the JSON below.\n"
        "Pick exactly one WINNER (A or B) for a typical consumer seeking "
        "strong hospitalization coverage.\n"
        "Write a blunt elimination_justification (2-4 sentences) explaining why the other policy loses.\n\n"
        "Reply with ONLY this JSON shape (no markdown, no reasoning preamble):\n"
        '{"winner":"A","elimination_justification":"..."}\n\n'
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
    message = response.choices[0].message
    raw = _groq_assistant_text(message)
    reasoning = (getattr(message, "reasoning", None) or "").strip()

    if not raw and reasoning:
        raw = reasoning
        logger.info("Compare used reasoning field because content was empty.")

    parsed = _parse_compare_response(raw)
    winner_side = parsed["winner"]
    winner_policy = policy_a if winner_side == "A" else policy_b
    loser_policy = policy_b if winner_side == "A" else policy_a

    scorecard_a = policy_to_response(policy_a)
    scorecard_b = policy_to_response(policy_b)

    return {
        "winner": winner_side,
        "winner_policy_id": winner_policy["policy_id"],
        "winner_filename": winner_policy.get("filename"),
        "loser_policy_id": loser_policy["policy_id"],
        "loser_filename": loser_policy.get("filename"),
        "elimination_justification": parsed["elimination_justification"],
        "policy_a": scorecard_a,
        "policy_b": scorecard_b,
    }
