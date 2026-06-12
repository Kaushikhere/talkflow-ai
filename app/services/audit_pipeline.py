"""Health insurance policy audit: extract metrics, store in SQLite, generate verdict."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import (
    AUDIT_ANALYSIS_MODEL,
    AUDIT_EXTRACTION_MODEL,
    AUDIT_MAX_TOKENS_EXTRACT,
    AUDIT_MAX_TOKENS_VERDICT,
)
from app.database import insert_uploaded_policy
from app.services.audit_pdf_parser import extract_audit_pdf_text
from app.services.audit_sources import build_sources_map, resolve_metric_source
from app.services.groq_client import get_groq_client

logger = logging.getLogger(__name__)

_EXTRACTION_SCHEMA = """{
  "room_rent_cap": "No cap | Single private AC | ... | unknown",
  "ped_waiting_period_months": 48,
  "co_payment_percentage": 20,
  "restoration_benefit": "100% once per year | Not mentioned | ...",
  "sub_limits": [],
  "maternity_waiting_months": null,
  "ambulance_cover": null,
  "notes": [],
  "_sources": {
    "room_rent_cap": {"page": 5, "excerpt": "verbatim sentence from document"},
    "ped_waiting_period_months": {"page": 12, "excerpt": "verbatim sentence from document"},
    "co_payment_percentage": {"page": 8, "excerpt": "verbatim sentence from document"},
    "restoration_benefit": {"page": 10, "excerpt": "verbatim sentence from document"}
  }
}"""

_METRIC_KEYS = (
    "room_rent_cap",
    "ped_waiting_period_months",
    "co_payment_percentage",
    "restoration_benefit",
)

_VERDICT_LABELS = frozenset({"BUY", "PASS", "REVIEW"})

_NO_ROOM_CAP_PHRASES = (
    "no cap",
    "no room rent",
    "not applicable",
    "unlimited",
    "none",
    "n/a",
    "no limit",
    "without cap",
)

_SEVERE_SUBLIMIT_KEYWORDS = (
    "sub-limit",
    "sublimit",
    "sub limit",
    "cap",
    "maximum",
    "limited to",
    "rs.",
    "inr",
    "% of sum",
    "percent of sum",
)


def _room_rent_has_cap(room_rent: str | None) -> bool | None:
    """Return True if capped, False if no cap, None if unknown."""
    if room_rent is None:
        return None
    text = str(room_rent).strip().lower()
    if not text or text == "unknown":
        return None
    if any(phrase in text for phrase in _NO_ROOM_CAP_PHRASES):
        return False
    return True


def _has_severe_sub_limits(sub_limits: Any) -> bool:
    if not sub_limits:
        return False
    items = sub_limits if isinstance(sub_limits, list) else [sub_limits]
    for item in items:
        text = str(item).lower()
        if any(kw in text for kw in _SEVERE_SUBLIMIT_KEYWORDS):
            return True
    return False


def _has_good_restoration(restoration: str | None) -> bool:
    if not restoration:
        return False
    text = str(restoration).lower()
    if "not mentioned" in text or text == "unknown":
        return False
    return any(token in text for token in ("100%", "full", "unlimited", "once per year", "restore"))


def _has_no_restoration(restoration: str | None) -> bool:
    if not restoration:
        return False
    text = str(restoration).lower()
    if "not mentioned" in text or "unknown" in text:
        return False
    return any(token in text for token in ("none", "no restoration", "not available", "not covered", "nil"))


def _classify_verdict_from_metrics(metrics: dict[str, Any]) -> str | None:
    """Deterministic BUY/REVIEW/PASS from extracted metrics; None if incomplete."""
    co_pay = metrics.get("co_payment_percentage")
    ped = metrics.get("ped_waiting_period_months")
    room_cap = _room_rent_has_cap(metrics.get("room_rent_cap"))
    sub_limits = metrics.get("sub_limits")
    restoration = metrics.get("restoration_benefit")

    if co_pay is None and room_cap is None and ped is None:
        return None

    if co_pay is not None and co_pay >= 15:
        return "PASS"
    if ped is not None and ped >= 48:
        return "PASS"
    if _has_no_restoration(restoration):
        return "PASS"
    if _has_severe_sub_limits(sub_limits):
        return "PASS"
    if room_cap is True:
        cap_text = str(metrics.get("room_rent_cap") or "").lower()
        if "%" in cap_text and any(token in cap_text for token in ("0.", "0,", "1%", "0.5%", "0.8%")):
            return "PASS"

    if (
        co_pay == 0
        and room_cap is False
        and not _has_severe_sub_limits(sub_limits)
        and ped is not None
        and ped <= 24
    ):
        return "BUY"

    if co_pay is not None and 1 <= co_pay <= 14:
        return "REVIEW"
    if ped is not None and 25 <= ped <= 47:
        return "REVIEW"
    if room_cap is True and (co_pay is None or co_pay <= 14):
        return "REVIEW"
    if _has_good_restoration(restoration) and room_cap is True:
        return "REVIEW"

    return None


def _parse_verdict_text_response(raw: str) -> dict[str, Any]:
    """Parse structured VERDICT / RECOMMENDATION SUMMARY / CRITICAL GAPS template."""
    text = raw.strip()
    fence = re.search(r"```(?:\w+)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()

    label = "REVIEW"
    verdict_match = re.search(r"VERDICT:\s*\[?\s*(BUY|PASS|REVIEW)\s*\]?", text, re.I)
    if verdict_match:
        label = verdict_match.group(1).upper()

    recommendation_summary = ""
    summary_match = re.search(
        r"RECOMMENDATION SUMMARY:\s*(.+?)(?=CRITICAL GAPS:|###|$)",
        text,
        re.I | re.S,
    )
    if summary_match:
        recommendation_summary = summary_match.group(1).strip()

    critical_gaps: list[str] = []
    gaps_match = re.search(r"CRITICAL GAPS:\s*(.+?)(?=###|$)", text, re.I | re.S)
    if gaps_match:
        for line in gaps_match.group(1).splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                critical_gaps.append(stripped[2:].strip())
            elif stripped.startswith("-"):
                critical_gaps.append(stripped[1:].strip())

    whats_missing = "\n".join(f"- {gap}" for gap in critical_gaps) if critical_gaps else ""

    return {
        "verdict_label": label,
        "recommendation_summary": recommendation_summary,
        "whats_missing": whats_missing,
        "key_risks": critical_gaps,
        "key_strengths": [],
    }


def _recommendation_headline(label: str) -> str:
    headlines = {
        "BUY": "RECOMMENDATION: BUY (Highly Cost-Effective)",
        "REVIEW": "RECOMMENDATION: PROCEED WITH CAUTION (Review Restrictions)",
        "PASS": "RECOMMENDATION: PASS (High Out-of-Pocket Risks)",
    }
    return headlines.get(label, headlines["REVIEW"])


def _parse_json_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        digits = re.search(r"\d+", value.replace(",", ""))
        if digits:
            return int(digits.group())
    return None


def _normalize_source_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    excerpt = str(entry.get("excerpt") or "").strip()
    if not excerpt:
        return None
    page = entry.get("page")
    if page is not None:
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = None
    return {"page": page, "excerpt": excerpt[:400]}


def _normalize_sources(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, entry in raw.items():
        normalized = _normalize_source_entry(entry)
        if normalized:
            out[str(key)] = normalized
    return out


def _normalize_metrics(data: dict[str, Any]) -> dict[str, Any]:
    room = data.get("room_rent_cap")
    if room is None or str(room).strip() == "":
        room = "unknown"

    restoration = data.get("restoration_benefit")
    if restoration is None or str(restoration).strip() == "":
        restoration = "Not mentioned"

    return {
        "room_rent_cap": str(room),
        "ped_waiting_period_months": _coerce_int(data.get("ped_waiting_period_months")),
        "co_payment_percentage": _coerce_int(data.get("co_payment_percentage")),
        "restoration_benefit": str(restoration),
        "sub_limits": list(data.get("sub_limits") or []),
        "maternity_waiting_months": _coerce_int(data.get("maternity_waiting_months")),
        "ambulance_cover": data.get("ambulance_cover"),
        "notes": list(data.get("notes") or []),
    }


def _parse_extraction(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    sources = _normalize_sources(data.pop("_sources", None))
    metrics = _normalize_metrics(data)
    return metrics, sources


def extract_policy_metrics(policy_text: str, *, retry: bool = True) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Phase 1: Llama 4 Scout extracts structured metrics and source citations."""
    prompt = (
        "You are a health insurance policy analyst. Extract ONLY factual metrics from "
        "the policy document text below.\n"
        "Rules:\n"
        "- Reply with ONLY valid JSON matching this schema (no markdown):\n"
        f"{_EXTRACTION_SCHEMA}\n"
        "- Use null for unknown numeric fields.\n"
        "- _sources: for each metric key, include page (integer from --- Page N --- markers) "
        "and excerpt (verbatim substring from the document, max 400 chars).\n"
        "- Do not invent values or excerpts not present in the text.\n\n"
        f"Policy document:\n{policy_text[:45000]}\n"
    )

    client = get_groq_client()
    raw = ""
    try:
        response = client.chat.completions.create(
            model=AUDIT_EXTRACTION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=AUDIT_MAX_TOKENS_EXTRACT,
        )
        raw = response.choices[0].message.content or ""
        data = _parse_json_response(raw)
        return _parse_extraction(data)
    except (json.JSONDecodeError, KeyError) as exc:
        if not retry:
            raise ValueError(f"Metric extraction returned invalid JSON: {exc}") from exc
        logger.warning("Extraction JSON parse failed, retrying once: %s", exc)
        repair_prompt = (
            "Fix the following into valid JSON matching the schema exactly. "
            "Output ONLY JSON:\n"
            f"{_EXTRACTION_SCHEMA}\n\nBroken output:\n{raw[:2000]}"
        )
        response = client.chat.completions.create(
            model=AUDIT_EXTRACTION_MODEL,
            messages=[{"role": "user", "content": repair_prompt}],
            temperature=0,
            max_tokens=AUDIT_MAX_TOKENS_EXTRACT,
        )
        raw = response.choices[0].message.content or ""
        return _parse_extraction(_parse_json_response(raw))
    except Exception as exc:
        logger.error("Metric extraction failed: %s", exc)
        raise


def generate_verdict(metrics: dict[str, Any]) -> dict[str, Any]:
    """Phase 2: GPT-OSS 20B produces Buy/Pass/Review verdict from structured metrics only."""
    metrics_json = json.dumps(metrics, indent=2)
    prompt = (
        "You are an expert, blunt, and completely independent health insurance auditor. "
        "Your job is to evaluate a policy's extracted metrics against ideal industry-standard "
        "benchmarks and deliver a definitive verdict.\n\n"
        "Here are the extracted metrics for the policy:\n"
        f"{metrics_json}\n\n"
        "Analyze the data strictly based on these consumer risk benchmarks:\n"
        "- [BUY] Criteria: Co-pay is 0%, No room rent caps or sub-limits, "
        "PED waiting period <= 24 months.\n"
        "- [REVIEW] Criteria: Minor or moderate restrictions exist (e.g., 36-month waiting "
        "period, 5-10% co-pay, room rent capped at a single private room, or 100% single "
        "annual restoration).\n"
        "- [PASS] Criteria: High financial risk for the consumer (e.g., Mandatory co-pay >= 15%, "
        "room rent caps limited to 1% of Sum Insured, waiting periods of 48 months, or no "
        "restoration benefits).\n\n"
        "You must format your response EXACTLY like the template below. Do not include any "
        "introductory prose, conversational filler, or markdown code blocks (like ```). "
        'Start directly with the text "VERDICT:".\n\n'
        "### TEMPLATE STRUCTURE TO FOLLOW UNIFORMLY ###\n"
        "VERDICT: [Insert exactly ONE of these words: BUY, PASS, or REVIEW]\n"
        "RECOMMENDATION SUMMARY: [A concise, 2-sentence direct advice statement telling the "
        "consumer exactly who this plan is suitable for, or what major financial exposure they "
        "face if they choose it.]\n"
        "CRITICAL GAPS:\n"
        "- [List 1-2 key features or protections that a premium, top-tier policy would have, "
        "but this specific policy is completely missing or restricting.]\n"
        "- [If applicable, mention another specific out-of-pocket exposure.]\n"
        "### END OF TEMPLATE ###"
    )

    client = get_groq_client()
    response = client.chat.completions.create(
        model=AUDIT_ANALYSIS_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=AUDIT_MAX_TOKENS_VERDICT,
    )
    raw = response.choices[0].message.content or ""
    if not raw.strip():
        raise ValueError("Verdict generation returned empty response.")

    data = _parse_verdict_text_response(raw)
    if not data.get("recommendation_summary") and not re.search(r"VERDICT:", raw, re.I):
        try:
            json_data = _parse_json_response(raw)
            data = {
                "verdict_label": json_data.get("verdict_label", "REVIEW"),
                "recommendation_summary": (
                    json_data.get("recommendation_summary") or json_data.get("verdict_text") or ""
                ),
                "whats_missing": json_data.get("whats_missing") or "",
                "key_risks": list(json_data.get("key_risks") or []),
                "key_strengths": list(json_data.get("key_strengths") or []),
            }
        except json.JSONDecodeError:
            logger.warning("Verdict parse incomplete; raw preview: %s", raw[:400])

    label = str(data.get("verdict_label") or "REVIEW").upper().strip()
    if label not in _VERDICT_LABELS:
        label = "REVIEW"

    rules_label = _classify_verdict_from_metrics(metrics)
    if rules_label is not None and (
        label == "REVIEW"
        or (rules_label == "PASS" and label != "PASS")
    ):
        if rules_label != label:
            logger.info(
                "Verdict override: LLM=%s rules=%s for metrics co_pay=%s room_cap=%s ped=%s",
                label,
                rules_label,
                metrics.get("co_payment_percentage"),
                metrics.get("room_rent_cap"),
                metrics.get("ped_waiting_period_months"),
            )
        label = rules_label

    recommendation_summary = str(data.get("recommendation_summary") or "").strip()
    whats_missing = str(data.get("whats_missing") or "").strip()
    verdict_text = recommendation_summary.split(".")[0].strip() if recommendation_summary else ""
    if verdict_text and not verdict_text.endswith("."):
        verdict_text += "."
    if not verdict_text:
        verdict_text = "Unable to generate a complete verdict from the extracted metrics."
    if not recommendation_summary:
        recommendation_summary = verdict_text

    key_risks = list(data.get("key_risks") or [])
    if not key_risks and whats_missing:
        key_risks = [
            line.lstrip("- ").strip()
            for line in whats_missing.splitlines()
            if line.strip().startswith("-")
        ]

    return {
        "verdict_label": label,
        "verdict_text": verdict_text,
        "recommendation_summary": recommendation_summary,
        "whats_missing": whats_missing,
        "key_risks": key_risks,
        "key_strengths": list(data.get("key_strengths") or []),
        "recommendation_headline": _recommendation_headline(label),
    }


def _metrics_for_storage(
    metrics: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    verdict_data: dict[str, Any],
) -> dict[str, Any]:
    return {
        **metrics,
        "_sources": sources,
        "_verdict_meta": {
            "key_risks": verdict_data.get("key_risks") or [],
            "key_strengths": verdict_data.get("key_strengths") or [],
            "recommendation_summary": verdict_data.get("recommendation_summary") or "",
            "whats_missing": verdict_data.get("whats_missing") or "",
            "recommendation_headline": verdict_data.get("recommendation_headline") or "",
        },
    }


def _parse_stored_metrics(raw_json: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        stored = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        return {}, {}, {}
    if not isinstance(stored, dict):
        return {}, {}, {}
    meta = stored.pop("_verdict_meta", None) or {}
    sources = stored.pop("_sources", None) or {}
    if not isinstance(meta, dict):
        meta = {}
    if not isinstance(sources, dict):
        sources = {}
    return stored, meta, sources


def policy_to_response(policy: dict) -> dict[str, Any]:
    """Serialize a DB policy row for API responses."""
    stored, meta, stored_sources = _parse_stored_metrics(policy.get("raw_extracted_json") or "{}")

    response = {
        "policy_id": policy["policy_id"],
        "filename": policy["filename"],
        "uploaded_at": policy["uploaded_at"],
        "metrics": {
            "room_rent_cap": policy.get("room_rent_cap"),
            "ped_waiting_period_months": policy.get("ped_waiting_period_months"),
            "co_payment_percentage": policy.get("co_payment_percentage"),
            "restoration_benefit": policy.get("restoration_benefit"),
            **{
                k: stored.get(k)
                for k in ("sub_limits", "maternity_waiting_months", "ambulance_cover", "notes")
            },
        },
        "verdict": policy.get("ai_verdict"),
        "verdict_label": policy.get("verdict_label"),
        "recommendation_summary": meta.get("recommendation_summary") or "",
        "whats_missing": meta.get("whats_missing") or "",
        "recommendation_headline": meta.get("recommendation_headline")
        or _recommendation_headline(str(policy.get("verdict_label") or "REVIEW").upper()),
        "key_risks": list(meta.get("key_risks") or []),
        "key_strengths": list(meta.get("key_strengths") or []),
        "sources": build_sources_map(
            policy,
            stored_sources=stored_sources,
            meta=meta,
        ),
    }
    return response


def get_policy_source(policy: dict, source_key: str) -> dict[str, Any] | None:
    """Resolve a single source citation for API lookup."""
    stored, meta, stored_sources = _parse_stored_metrics(policy.get("raw_extracted_json") or "{}")
    extracted = policy.get("extracted_text") or ""

    if source_key in _METRIC_KEYS:
        value = policy.get(source_key)
        src = resolve_metric_source(source_key, value, extracted, stored_sources=stored_sources)
    elif source_key.startswith("risk_"):
        try:
            idx = int(source_key.split("_", 1)[1])
            risks = meta.get("key_risks") or []
            value = risks[idx] if idx < len(risks) else None
        except (ValueError, IndexError):
            return None
        src = resolve_metric_source(source_key, value, extracted, stored_sources=stored_sources)
    elif source_key.startswith("strength_"):
        try:
            idx = int(source_key.split("_", 1)[1])
            strengths = meta.get("key_strengths") or []
            value = strengths[idx] if idx < len(strengths) else None
        except (ValueError, IndexError):
            return None
        src = resolve_metric_source(source_key, value, extracted, stored_sources=stored_sources)
    else:
        src = resolve_metric_source(source_key, None, extracted, stored_sources=stored_sources)

    if not src:
        return None

    return {
        "metric_key": source_key,
        "page": src.get("page"),
        "excerpt": src.get("excerpt"),
        "approximate": bool(src.get("approximate")),
    }


def run_audit_pipeline(file_path: Path, filename: str) -> dict[str, Any]:
    """Full pipeline: parse PDF → extract metrics → save → verdict."""
    extracted_text = extract_audit_pdf_text(file_path)
    if not extracted_text.strip():
        raise ValueError("Could not extract readable text from the PDF.")

    metrics, sources = extract_policy_metrics(extracted_text)

    for key in _METRIC_KEYS:
        if key not in sources:
            fallback = resolve_metric_source(key, metrics.get(key), extracted_text)
            if fallback:
                sources[key] = fallback

    verdict_data = generate_verdict(metrics)

    policy_id = insert_uploaded_policy(
        filename=filename,
        stored_path=str(file_path.resolve()),
        room_rent_cap=metrics.get("room_rent_cap"),
        ped_waiting_period_months=metrics.get("ped_waiting_period_months"),
        co_payment_percentage=metrics.get("co_payment_percentage"),
        restoration_benefit=metrics.get("restoration_benefit"),
        raw_extracted_json=json.dumps(_metrics_for_storage(metrics, sources, verdict_data)),
        extracted_text=extracted_text,
        ai_verdict=verdict_data["verdict_text"],
        verdict_label=verdict_data["verdict_label"],
    )

    policy_row = {
        "policy_id": policy_id,
        "filename": filename,
        "uploaded_at": "",
        "room_rent_cap": metrics.get("room_rent_cap"),
        "ped_waiting_period_months": metrics.get("ped_waiting_period_months"),
        "co_payment_percentage": metrics.get("co_payment_percentage"),
        "restoration_benefit": metrics.get("restoration_benefit"),
        "raw_extracted_json": json.dumps(_metrics_for_storage(metrics, sources, verdict_data)),
        "extracted_text": extracted_text,
        "ai_verdict": verdict_data["verdict_text"],
        "verdict_label": verdict_data["verdict_label"],
    }

    return {
        "policy_id": policy_id,
        "filename": filename,
        "metrics": metrics,
        "verdict": verdict_data["verdict_text"],
        "verdict_label": verdict_data["verdict_label"],
        "recommendation_summary": verdict_data["recommendation_summary"],
        "whats_missing": verdict_data["whats_missing"],
        "recommendation_headline": verdict_data["recommendation_headline"],
        "key_risks": verdict_data["key_risks"],
        "key_strengths": verdict_data["key_strengths"],
        "sources": build_sources_map(policy_row, stored_sources=sources, meta={
            "key_risks": verdict_data["key_risks"],
            "key_strengths": verdict_data["key_strengths"],
            "recommendation_summary": verdict_data["recommendation_summary"],
            "whats_missing": verdict_data["whats_missing"],
        }),
    }
