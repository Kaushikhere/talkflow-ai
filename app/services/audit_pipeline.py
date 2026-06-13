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
from app.services.audit_market_data import (
    build_market_context_block,
    resolve_evaluation_profile,
)
from app.services.audit_pincode import enrich_metrics_with_pincode
from app.services.audit_pdf_parser import extract_audit_pdf_text
from app.services.audit_sources import build_sources_map, resolve_metric_source
from app.services.groq_client import get_groq_client, groq_assistant_text

logger = logging.getLogger(__name__)

_EXTRACTION_SCHEMA = """{
  "policy_pincode": 302001,
  "policy_city": "Jaipur | Mumbai | Delhi | unknown",
  "policy_zone": "Zone A | Zone B | Category I | unknown",
  "policy_tier": "Tier 1 | Tier 2 | Tier 3 | unknown",
  "sum_insured_amount": 500000,
  "room_rent_cap": "No cap | Single private AC | 1% of sum insured | ... | unknown",
  "room_rent_cap_daily_inr": 5000,
  "ped_waiting_period_months": 48,
  "co_payment_percentage": 20,
  "restoration_benefit": "100% once per year | Not mentioned | ...",
  "consumables_excluded": true,
  "zonal_co_pay": "None | 10% co-pay for metro treatment when policy zone is lower | unknown",
  "sub_limits": [],
  "maternity_waiting_months": null,
  "ambulance_cover": null,
  "notes": [],
  "_sources": {
    "policy_pincode": {"page": 1, "excerpt": "verbatim sentence from document"},
    "policy_city": {"page": 1, "excerpt": "verbatim sentence from document"},
    "policy_zone": {"page": 1, "excerpt": "verbatim sentence from document"},
    "sum_insured_amount": {"page": 2, "excerpt": "verbatim sentence from document"},
    "room_rent_cap": {"page": 5, "excerpt": "verbatim sentence from document"},
    "ped_waiting_period_months": {"page": 12, "excerpt": "verbatim sentence from document"},
    "co_payment_percentage": {"page": 8, "excerpt": "verbatim sentence from document"},
    "restoration_benefit": {"page": 10, "excerpt": "verbatim sentence from document"},
    "consumables_excluded": {"page": 15, "excerpt": "verbatim sentence from document"},
    "zonal_co_pay": {"page": 6, "excerpt": "verbatim sentence from document"}
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


def _parse_room_rent_cap_inr(metrics: dict[str, Any], profile: dict[str, Any] | None) -> int | None:
    """Best-effort daily room rent cap in INR from extracted metrics."""
    daily = metrics.get("room_rent_cap_daily_inr")
    if daily is not None:
        return int(daily)

    cap_text = str(metrics.get("room_rent_cap") or "").lower()
    if not cap_text or cap_text == "unknown":
        return None
    if any(phrase in cap_text for phrase in _NO_ROOM_CAP_PHRASES):
        return None

    fixed = re.search(r"(?:rs\.?|inr|₹)\s*([\d,]+)\s*(?:/day|per day|daily)?", cap_text, re.I)
    if fixed:
        return int(fixed.group(1).replace(",", ""))

    pct = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:of\s+)?(?:sum\s+insured|si)", cap_text, re.I)
    if pct:
        sum_insured = metrics.get("sum_insured_amount")
        if sum_insured is None and profile:
            sum_insured = profile.get("user_sum_insured")
        if sum_insured:
            return int(float(pct.group(1)) / 100 * int(sum_insured))

    return None


def _classify_verdict_from_metrics(
    metrics: dict[str, Any],
    profile: dict[str, Any] | None = None,
) -> str | None:
    """Deterministic BUY/REVIEW/PASS from extracted metrics; None if incomplete."""
    co_pay = metrics.get("co_payment_percentage")
    ped = metrics.get("ped_waiting_period_months")
    room_cap = _room_rent_has_cap(metrics.get("room_rent_cap"))
    sub_limits = metrics.get("sub_limits")
    restoration = metrics.get("restoration_benefit")

    if co_pay is None and room_cap is None and ped is None:
        return None

    if profile:
        tier = profile.get("city_tier", "Tier 1")
        min_si = int(profile.get("min_sum_insured_benchmark") or 0)
        user_si = int(profile.get("user_sum_insured") or metrics.get("sum_insured_amount") or 0)
        policy_si = metrics.get("sum_insured_amount")
        effective_si = int(policy_si) if policy_si else user_si
        if min_si and effective_si and effective_si < min_si:
            if tier == "Tier 1" and effective_si < 1_000_000:
                return "PASS"
            if tier == "Tier 2" and effective_si < 500_000:
                return "PASS"
            if tier == "Tier 3" and effective_si < 300_000:
                return "PASS"

        local_room = int(profile.get("local_room_cost") or 0)
        cap_inr = _parse_room_rent_cap_inr(metrics, profile)
        if local_room and cap_inr and cap_inr < local_room * 0.85:
            return "PASS"

        zonal = str(metrics.get("zonal_co_pay") or "").lower()
        if zonal and zonal not in ("none", "unknown", "not applicable", "n/a", "nil"):
            if any(token in zonal for token in ("10%", "15%", "20%", "metro", "tier 1", "zone a")):
                if tier in ("Tier 2", "Tier 3"):
                    return "REVIEW"

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


_GAP_PLACEHOLDER_VALUES = frozenset({
    "none",
    "n/a",
    "na",
    "nil",
    "not applicable",
    "no gap",
    "no gaps",
    "no major gaps",
    "no significant gaps",
    "nothing",
    "not identified",
})


def _is_real_gap_item(text: str) -> bool:
    """Return False for template placeholders like 'None' that are not real gaps."""
    cleaned = re.sub(r"^\[[^\]]*\]\s*", "", str(text or "").strip())
    cleaned = re.sub(r"^flag\s*\d+\s*:\s*", "", cleaned, flags=re.I).strip()
    cleaned = cleaned.strip("\"' ")
    if not cleaned:
        return False
    normalized = cleaned.lower().rstrip(".")
    if normalized in _GAP_PLACEHOLDER_VALUES:
        return False
    if re.fullmatch(r"none(\s+identified)?", normalized):
        return False
    return True


def _filter_real_gaps(items: list[str]) -> list[str]:
    return [item for item in items if _is_real_gap_item(item)]


_WAITING_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "pediatric": ("pediatric", "paediatric", "child care", "children", "infant", "newborn"),
    "maternity": ("maternity", "pregnancy", "childbirth", "delivery", "natal"),
}


def _metrics_evidence_blob(metrics: dict[str, Any]) -> str:
    return json.dumps(metrics, default=str).lower()


def _claim_is_grounded_in_metrics(claim: str, metrics: dict[str, Any]) -> bool:
    """Reject verdict claims that cite waiting periods or benefits not in extracted JSON."""
    claim_l = claim.lower()
    blob = _metrics_evidence_blob(metrics)

    for topic, keywords in _WAITING_TOPIC_KEYWORDS.items():
        if any(kw in claim_l for kw in keywords) and not any(kw in blob for kw in keywords):
            logger.info("Ungrounded verdict claim dropped (%s not in metrics): %s", topic, claim[:80])
            return False

    if "pediatric" in claim_l or "paediatric" in claim_l:
        if "pediatric" not in blob and "paediatric" not in blob and "child" not in blob:
            logger.info("Ungrounded pediatric claim dropped: %s", claim[:80])
            return False

    waiting_context = any(
        token in claim_l
        for token in ("wait", "waiting", "period", "ped", "pre-existing", "pre existing")
    )
    if waiting_context:
        for match in re.finditer(r"(\d+)\s*[- ]?\s*month", claim_l):
            month = match.group(1)
            ped = metrics.get("ped_waiting_period_months")
            mat = metrics.get("maternity_waiting_months")
            known_months = {str(v) for v in (ped, mat) if v is not None}
            extras = " ".join(
                str(x)
                for x in (metrics.get("sub_limits") or []) + (metrics.get("notes") or [])
            ).lower()
            if month not in known_months and month not in extras and month not in blob:
                logger.info("Ungrounded %s-month waiting claim dropped: %s", month, claim[:80])
                return False

    return True


def _scrub_ungrounded_text(text: str, metrics: dict[str, Any]) -> str:
    if not text.strip():
        return text
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    kept = [part for part in parts if _claim_is_grounded_in_metrics(part, metrics)]
    return " ".join(kept).strip() if kept else text.strip()


def _sanitize_verdict_against_metrics(
    *,
    recommendation_summary: str,
    strategic_verdict: str,
    key_risks: list[str],
    metrics: dict[str, Any],
) -> tuple[str, str, list[str]]:
    grounded_risks = [gap for gap in key_risks if _claim_is_grounded_in_metrics(gap, metrics)]
    summary = _scrub_ungrounded_text(recommendation_summary, metrics)
    strategic = _scrub_ungrounded_text(strategic_verdict, metrics)
    return summary, strategic, grounded_risks


def _parse_verdict_text_response(raw: str) -> dict[str, Any]:
    """Parse geo-aware RECOMMENDATION / The Verdict / Critical Gaps / STRATEGIC VERDICT template."""
    text = raw.strip()
    fence = re.search(r"```(?:\w+)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()

    label = "REVIEW"
    recommendation_headline = ""

    rec_match = re.search(
        r"RECOMMENDATION:\s*\[?\s*(BUY|PASS|REVIEW)\s*\]?"
        r"(?:\s*\(([^)]+)\))?",
        text,
        re.I,
    )
    if rec_match:
        label = rec_match.group(1).upper()
        risk_note = (rec_match.group(2) or "").strip()
        recommendation_headline = f"RECOMMENDATION: {label}"
        if risk_note:
            recommendation_headline += f" ({risk_note})"
    else:
        verdict_match = re.search(r"VERDICT:\s*\[?\s*(BUY|PASS|REVIEW)\s*\]?", text, re.I)
        if verdict_match:
            label = verdict_match.group(1).upper()

    recommendation_summary = ""
    verdict_match = re.search(
        r"The Verdict:\s*(.+?)(?=Critical Gaps:|CRITICAL GAPS:|STRATEGIC VERDICT|###|$)",
        text,
        re.I | re.S,
    )
    if verdict_match:
        recommendation_summary = verdict_match.group(1).strip()
    else:
        summary_match = re.search(
            r"RECOMMENDATION SUMMARY:\s*(.+?)(?=Critical Gaps:|CRITICAL GAPS:|###|$)",
            text,
            re.I | re.S,
        )
        if summary_match:
            recommendation_summary = summary_match.group(1).strip()

    strategic_verdict = ""
    strategic_match = re.search(
        r"STRATEGIC VERDICT\s*\n?\s*(.+?)(?=###|$)",
        text,
        re.I | re.S,
    )
    if strategic_match:
        strategic_verdict = strategic_match.group(1).strip()

    critical_gaps: list[str] = []
    gaps_match = re.search(
        r"Critical Gaps:\s*(.+?)(?=STRATEGIC VERDICT|###|$)",
        text,
        re.I | re.S,
    )
    if not gaps_match:
        gaps_match = re.search(r"CRITICAL GAPS:\s*(.+?)(?=STRATEGIC VERDICT|###|$)", text, re.I | re.S)
    if gaps_match:
        for line in gaps_match.group(1).splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                critical_gaps.append(stripped[2:].strip())
            elif stripped.startswith("-"):
                critical_gaps.append(stripped[1:].strip())

    critical_gaps = _filter_real_gaps(critical_gaps)
    whats_missing = "\n".join(f"- {gap}" for gap in critical_gaps) if critical_gaps else ""

    return {
        "verdict_label": label,
        "recommendation_headline": recommendation_headline,
        "recommendation_summary": recommendation_summary,
        "strategic_verdict": strategic_verdict,
        "whats_missing": whats_missing,
        "key_risks": critical_gaps,
        "key_strengths": [],
    }


def _recommendation_headline(label: str, *, high_oop: bool = True) -> str:
    risk = "High Out-of-Pocket Risks" if high_oop and label == "PASS" else (
        "Low Out-of-Pocket Risks" if label == "BUY" else "Moderate Out-of-Pocket Risks"
    )
    headlines = {
        "BUY": f"RECOMMENDATION: BUY ({risk})",
        "REVIEW": f"RECOMMENDATION: REVIEW ({risk})",
        "PASS": f"RECOMMENDATION: PASS ({risk})",
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


_UNKNOWN_METRIC_VALUES = frozenset({"", "unknown", "not mentioned", "not stated", "n/a", "na", "null", "none"})

_CONSUMABLES_EXCLUDED_PHRASES = (
    "non-medical",
    "non medical",
    "nonmedical",
    "consumable",
    "syringe",
    "needle",
    "ppe",
    "glove",
    "bandage",
    "iv kit",
    "diaper",
    "toiletries",
    "beauty",
    "food charge",
    "attendant",
    "service charge",
    "admission kit",
)

_CONSUMABLES_COVERED_PHRASES = (
    "consumables are covered",
    "non-medical expenses are covered",
    "including consumables",
    "covers non-medical",
)


def _is_unknown_metric(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in _UNKNOWN_METRIC_VALUES


def _scan_consumables_from_text(policy_text: str) -> bool | None:
    lower = policy_text.lower()
    if any(phrase in lower for phrase in _CONSUMABLES_COVERED_PHRASES):
        return False
    if any(phrase in lower for phrase in _CONSUMABLES_EXCLUDED_PHRASES):
        return True
    return None


def _infer_policy_zone_label(metrics: dict[str, Any], profile: dict[str, Any]) -> str:
    tier = profile.get("city_tier") or "Tier 3"
    locality = profile.get("locality_type") or "town"
    pin = profile.get("policy_pincode")
    zonal = str(metrics.get("zonal_co_pay") or "")

    if not _is_unknown_metric(zonal):
        return f"{tier} ({locality}) — geographic co-pay: {zonal}"

    district = profile.get("policy_district") or profile.get("policy_city")
    if pin and district:
        return f"{tier} ({locality}) — {district}, pincode {pin}"
    if pin:
        return f"{tier} ({locality}) — pincode {pin}"
    return f"{tier} ({locality})"


def _parse_base_copay_from_zonal(zonal_text: str) -> int | None:
    """Pick the lowest stated co-pay % (usually home district) from zonal clause."""
    percents = [int(match) for match in re.findall(r"(\d+)\s*%\s*co-?pay", zonal_text, re.I)]
    if not percents:
        percents = [int(match) for match in re.findall(r"co-?pay(?:ment)?\s*(?:of\s*)?(\d+)\s*%", zonal_text, re.I)]
    return min(percents) if percents else None


def enrich_metrics_with_context(
    metrics: dict[str, Any],
    policy_text: str,
    profile: dict[str, Any],
    sources: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Fill gaps using pincode profile + policy text when extraction returns unknown."""
    enriched = dict(metrics)
    src = dict(sources)

    if _is_unknown_metric(enriched.get("policy_city")):
        city = profile.get("policy_city") or profile.get("policy_district")
        if city:
            enriched["policy_city"] = city
            pin_suffix = (
                f" (pincode {profile.get('policy_pincode')})"
                if profile.get("policy_pincode")
                else ""
            )
            src.setdefault("policy_city", {
                "page": None,
                "excerpt": f"Resolved from geographic analysis{pin_suffix} → {city}",
                "approximate": True,
            })

    if _is_unknown_metric(enriched.get("policy_tier")) and profile.get("city_tier"):
        enriched["policy_tier"] = profile["city_tier"]
        src.setdefault("policy_tier", {
            "page": None,
            "excerpt": (
                f"Classified as {profile['city_tier']} ({profile.get('locality_type') or 'town'}) "
                f"via geographic analysis for this policy location"
            ),
            "approximate": True,
        })

    if _is_unknown_metric(enriched.get("policy_zone")):
        enriched["policy_zone"] = _infer_policy_zone_label(enriched, profile)
        src.setdefault("policy_zone", {
            "page": None,
            "excerpt": enriched["policy_zone"],
            "approximate": True,
        })

    if enriched.get("consumables_excluded") is None:
        scanned = _scan_consumables_from_text(policy_text)
        if scanned is not None:
            enriched["consumables_excluded"] = scanned
            label = "excluded" if scanned else "covered"
            src.setdefault("consumables_excluded", {
                "page": None,
                "excerpt": f"Detected from policy text: non-medical consumables appear {label}",
                "approximate": True,
            })

    if enriched.get("co_payment_percentage") is None:
        zonal = str(enriched.get("zonal_co_pay") or "")
        if not _is_unknown_metric(zonal):
            base_copay = _parse_base_copay_from_zonal(zonal)
            if base_copay is not None:
                enriched["co_payment_percentage"] = base_copay
                src.setdefault("co_payment_percentage", {
                    "page": src.get("zonal_co_pay", {}).get("page"),
                    "excerpt": f"Lowest co-pay in zonal clause: {base_copay}% ({zonal[:200]})",
                    "approximate": True,
                })

    return enriched, src


def _normalize_metrics(data: dict[str, Any]) -> dict[str, Any]:
    room = data.get("room_rent_cap")
    if room is None or str(room).strip() == "":
        room = "unknown"

    restoration = data.get("restoration_benefit")
    if restoration is None or str(restoration).strip() == "":
        restoration = "Not mentioned"

    consumables = data.get("consumables_excluded")
    if isinstance(consumables, str):
        consumables = consumables.strip().lower() in ("true", "yes", "excluded", "1")

    zonal = data.get("zonal_co_pay")
    if zonal is None or str(zonal).strip() == "":
        zonal = "unknown"

    policy_city = data.get("policy_city")
    if policy_city is None or str(policy_city).strip() == "":
        policy_city = "unknown"

    policy_pincode = _coerce_int(data.get("policy_pincode"))

    policy_zone = data.get("policy_zone")
    if policy_zone is None or str(policy_zone).strip() == "":
        policy_zone = "unknown"

    policy_tier = data.get("policy_tier")
    if policy_tier is None or str(policy_tier).strip() == "":
        policy_tier = "unknown"

    return {
        "policy_pincode": policy_pincode,
        "policy_city": str(policy_city),
        "policy_zone": str(policy_zone),
        "policy_tier": str(policy_tier),
        "sum_insured_amount": _coerce_int(data.get("sum_insured_amount")),
        "room_rent_cap": str(room),
        "room_rent_cap_daily_inr": _coerce_int(data.get("room_rent_cap_daily_inr")),
        "ped_waiting_period_months": _coerce_int(data.get("ped_waiting_period_months")),
        "co_payment_percentage": _coerce_int(data.get("co_payment_percentage")),
        "restoration_benefit": str(restoration),
        "consumables_excluded": bool(consumables) if consumables is not None else None,
        "zonal_co_pay": str(zonal),
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
        "- policy_pincode: 6-digit Indian postal pincode from proposer/resident/correspondence "
        "address (India only). Use null if not stated.\n"
        "- policy_city: city explicitly tied to this policy (proposer/resident address, registered "
        "city, primary insured location, or the main city named in the insurer's zone table). "
        "Use 'unknown' if not stated.\n"
        "- policy_zone: insurer zone label (Zone A/B/C) OR a short summary of geographic pricing "
        "from co-pay clauses (e.g. '5% in home district, 15% in Tier-1 metro'). Use 'unknown' only "
        "if no geographic pricing structure exists.\n"
        "- policy_tier: Tier 1/2/3 if explicitly stated; else 'unknown'.\n"
        "- consumables_excluded: true if non-medical/consumables/syringes/PPE/gloves are excluded; "
        "false if explicitly covered; null if the document is silent.\n"
        "- co_payment_percentage: numeric base co-pay if stated; if only zonal co-pay exists, use the "
        "lowest percentage (usually home-district rate).\n"
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
        raw = groq_assistant_text(response.choices[0].message) or ""
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
        raw = groq_assistant_text(response.choices[0].message) or ""
        return _parse_extraction(_parse_json_response(raw))
    except Exception as exc:
        logger.error("Metric extraction failed: %s", exc)
        raise


def _fallback_verdict_data(
    metrics: dict[str, Any],
    profile: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    """Rule-based verdict text when the LLM returns nothing parseable."""
    city = profile.get("policy_city") or profile.get("user_city") or "the policy location"
    tier = profile.get("city_tier", "Tier 3")
    room = int(profile.get("local_room_cost") or 2000)
    si = int(profile.get("user_sum_insured") or metrics.get("sum_insured_amount") or 0)
    min_si = int(profile.get("min_sum_insured_benchmark") or 300_000)
    room_cap = metrics.get("room_rent_cap") or "unknown"
    co_pay = metrics.get("co_payment_percentage")

    summary = (
        f"For {city} ({tier}), this policy was scored against ₹{room:,}/day local room costs "
        f"and a ₹{min_si:,} minimum recommended sum insured. "
        f"Extracted cover is ₹{si:,} with room rent cap '{room_cap}'"
        f"{f' and {co_pay}% co-pay' if co_pay is not None else ''}."
    )
    gaps = [
        f"Sum insured ₹{si:,} vs {tier} benchmark ₹{min_si:,}"
        if si and si < min_si
        else f"Sum insured adequacy for {tier} (₹{min_si:,} benchmark)",
        "Review room rent cap vs local hospital room costs for proportionate deduction risk",
        "Confirm consumables rider if non-medical items are excluded",
    ]
    whats_missing = "\n".join(f"- {gap}" for gap in gaps)
    strategic = (
        "Automated rule assessment applied because the AI verdict was empty. "
        "Re-upload or check GROQ_API_KEY and AUDIT_MAX_TOKENS_VERDICT if this persists."
    )
    return {
        "verdict_label": label,
        "recommendation_headline": _recommendation_headline(label, high_oop=(label == "PASS")),
        "recommendation_summary": summary,
        "strategic_verdict": strategic,
        "whats_missing": whats_missing,
        "key_risks": gaps,
        "key_strengths": [],
    }


def generate_verdict(
    metrics: dict[str, Any],
    user_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Phase 2: geo-aware underwriter verdict from metrics + local market benchmarks."""
    profile = user_profile or resolve_evaluation_profile(metrics)
    metrics_json = json.dumps(metrics, indent=2)
    market_block = build_market_context_block(profile)
    eval_city = profile.get("policy_city") or profile.get("user_city") or "the policy's city"
    eval_pin = profile.get("policy_pincode")
    pin_line = f"- Policy pincode (from document): {eval_pin}\n" if eval_pin else ""
    district_line = ""
    if profile.get("policy_district"):
        district_line = (
            f"- Resolved location: {profile.get('policy_district')}"
            f"{', ' + profile['policy_state'] if profile.get('policy_state') else ''}\n"
        )
    prompt = (
        "You are an expert, blunt Indian health insurance underwriter and consumer advocate. "
        "Your job is to analyze an extracted policy schema against the geographic and economic "
        "reality of the pincode/city/zone stated IN THE POLICY DOCUMENT to determine if this "
        "policy is a financial trap or a safe buy.\n\n"
        "### POLICY GEOGRAPHIC CONTEXT (from document + benchmarks) ###\n"
        f"{pin_line}"
        f"{district_line}"
        f"- Locality type: {profile.get('locality_type') or 'unknown'}\n"
        f"- Policy city: {eval_city} (Zone / Tier: {profile['city_tier']})\n"
        f"- Policy zone: {profile.get('policy_zone') or 'Not stated'}\n"
        f"- Expected Average Local Hospital Room Cost: ₹{profile['local_room_cost']:,} per day\n"
        f"- Policy Sum Insured: ₹{profile['user_sum_insured']:,}\n\n"
        f"{market_block}\n\n"
        "### EXTRACTED POLICY METRICS (JSON) ###\n"
        f"{metrics_json}\n\n"
        "### EVALUATION RULES & MARKET BENCHMARKS ###\n"
        "1. SUM INSURED ADEQUACY: Tier 1 Metro minimum ₹10 Lakhs; Tier 2 minimum ₹5 Lakhs; "
        "Tier 3 town/village minimum ₹3 Lakhs.\n"
        "2. THE ROOM RENT TRAP: Compare the policy's room rent cap against the Expected Average "
        "Local Hospital Room Cost. If the policy cap is lower, warn heavily about "
        '"Proportionate Deduction"—where the insurer cuts the entire hospital bill proportionally, '
        "not just the room difference.\n"
        '3. CONSUMABLES & RIDER GAP: If the policy excludes non-medical consumables (PPE kits, '
        'gloves, syringes), state that a "Consumables/Safeguard Rider" is mandatory.\n'
        "4. ZONAL CO-PAYMENT: Flag if a policy bought in a lower tier penalizes the user with a "
        "co-pay when treated in a Tier 1 city hospital.\n\n"
        "CRITICAL ANTI-HALLUCINATION RULE: Use ONLY facts present in EXTRACTED POLICY METRICS JSON. "
        "Do NOT invent pediatric, maternity, disease-specific, or other waiting periods unless "
        "explicitly present in ped_waiting_period_months, maternity_waiting_months, sub_limits, "
        "or notes. PED means pre-existing disease waiting — not pediatric/child coverage.\n\n"
        "Format your output EXACTLY like the structure below. Do not include introductory filler, "
        "conversational pleasantries, or markdown code blocks. Start directly with "
        '"RECOMMENDATION:".\n\n'
        "### REQUIRED OUTPUT FORMAT ###\n\n"
        f"RECOMMENDATION: [Insert exactly ONE: BUY, REVIEW, or PASS] (High/Low Out-of-Pocket Risks)\n\n"
        f"The Verdict: [A definitive, 2-sentence explanation of why this plan succeeds or fails "
        f"specifically for someone covered under this policy in {eval_city}, citing the exact room "
        f"rent gap or sum insured adequacy.]\n\n"
        "Critical Gaps:\n"
        f"- [Flag 1: Localized financial risk, e.g., sum insured vs {profile['city_tier']} minimum.]\n"
        "- [Flag 2: Room rent or Proportionate Deduction warning]\n"
        "- [Flag 3: Waiting periods or Zonal Co-pay traps]\n"
        "Omit any bullet entirely if that category does not apply — never write 'None' as a bullet.\n\n"
        "STRATEGIC VERDICT\n"
        "[Provide a closing 2-sentence summary detailing the hidden terms and conditions "
        "consequences. Explicitly state whether the user needs to purchase specific Add-ons/Riders "
        "(like a Consumables Rider) to make this policy viable, or if they should abandon it entirely.]"
    )

    client = get_groq_client()

    def _request_verdict(user_prompt: str, *, max_tokens: int) -> tuple[str, str | None]:
        response = client.chat.completions.create(
            model=AUDIT_ANALYSIS_MODEL,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        choice = response.choices[0]
        raw = groq_assistant_text(choice.message)
        finish = getattr(choice, "finish_reason", None)
        if not raw.strip() and getattr(choice.message, "reasoning", None):
            logger.info("Verdict used reasoning field because content was empty.")
        return raw, finish

    raw, finish_reason = _request_verdict(prompt, max_tokens=AUDIT_MAX_TOKENS_VERDICT)
    if not raw.strip():
        retry_prompt = (
            f"{prompt}\n\nIMPORTANT: Reply with ONLY the RECOMMENDATION / The Verdict / "
            "Critical Gaps / STRATEGIC VERDICT sections. No chain-of-thought or preamble."
        )
        raw, finish_reason = _request_verdict(
            retry_prompt,
            max_tokens=max(AUDIT_MAX_TOKENS_VERDICT, 1536),
        )

    if not raw.strip():
        rules_label = _classify_verdict_from_metrics(metrics, profile) or "REVIEW"
        logger.warning(
            "Verdict LLM returned empty twice; using rule fallback label=%s finish=%s",
            rules_label,
            finish_reason,
        )
        fallback = _fallback_verdict_data(metrics, profile, rules_label)
        fallback["user_profile"] = profile
        return {
            "verdict_label": fallback["verdict_label"],
            "verdict_text": fallback["recommendation_summary"].split(".")[0].strip() + ".",
            **fallback,
            "user_profile": profile,
        }

    data = _parse_verdict_text_response(raw)
    if not data.get("recommendation_summary") and not re.search(
        r"RECOMMENDATION:|VERDICT:", raw, re.I
    ):
        try:
            json_data = _parse_json_response(raw)
            data = {
                "verdict_label": json_data.get("verdict_label", "REVIEW"),
                "recommendation_headline": json_data.get("recommendation_headline") or "",
                "recommendation_summary": (
                    json_data.get("recommendation_summary") or json_data.get("verdict_text") or ""
                ),
                "strategic_verdict": json_data.get("strategic_verdict") or "",
                "whats_missing": json_data.get("whats_missing") or "",
                "key_risks": list(json_data.get("key_risks") or []),
                "key_strengths": list(json_data.get("key_strengths") or []),
            }
        except json.JSONDecodeError:
            logger.warning("Verdict parse incomplete; raw preview: %s", raw[:400])

    label = str(data.get("verdict_label") or "REVIEW").upper().strip()
    if label not in _VERDICT_LABELS:
        label = "REVIEW"

    rules_label = _classify_verdict_from_metrics(metrics, profile)
    if rules_label is not None and (
        label == "REVIEW"
        or (rules_label == "PASS" and label != "PASS")
    ):
        if rules_label != label:
            logger.info(
                "Verdict override: LLM=%s rules=%s city=%s si=%s room_cap=%s",
                label,
                rules_label,
                profile.get("user_city"),
                profile.get("user_sum_insured"),
                metrics.get("room_rent_cap"),
            )
        label = rules_label

    recommendation_summary = str(data.get("recommendation_summary") or "").strip()
    strategic_verdict = str(data.get("strategic_verdict") or "").strip()
    whats_missing = str(data.get("whats_missing") or "").strip()
    verdict_text = recommendation_summary.split(".")[0].strip() if recommendation_summary else ""
    if verdict_text and not verdict_text.endswith("."):
        verdict_text += "."
    if not verdict_text:
        verdict_text = "Unable to generate a complete verdict from the extracted metrics."
    if not recommendation_summary:
        recommendation_summary = verdict_text
    if not strategic_verdict:
        strategic_verdict = recommendation_summary

    key_risks = _filter_real_gaps(list(data.get("key_risks") or []))
    if not key_risks and whats_missing:
        key_risks = _filter_real_gaps([
            line.lstrip("- ").strip()
            for line in whats_missing.splitlines()
            if line.strip().startswith("-")
        ])

    recommendation_summary, strategic_verdict, key_risks = _sanitize_verdict_against_metrics(
        recommendation_summary=recommendation_summary,
        strategic_verdict=strategic_verdict,
        key_risks=key_risks,
        metrics=metrics,
    )
    whats_missing = "\n".join(f"- {gap}" for gap in key_risks) if key_risks else ""
    verdict_text = recommendation_summary.split(".")[0].strip() if recommendation_summary else ""
    if verdict_text and not verdict_text.endswith("."):
        verdict_text += "."

    headline = str(data.get("recommendation_headline") or "").strip()
    headline_label_match = re.search(r"RECOMMENDATION:\s*(BUY|REVIEW|PASS)", headline, re.I)
    if not headline or (headline_label_match and headline_label_match.group(1).upper() != label):
        headline = _recommendation_headline(label, high_oop=(label == "PASS"))

    return {
        "verdict_label": label,
        "verdict_text": verdict_text,
        "recommendation_summary": recommendation_summary,
        "strategic_verdict": strategic_verdict,
        "whats_missing": whats_missing,
        "key_risks": key_risks,
        "key_strengths": list(data.get("key_strengths") or []),
        "recommendation_headline": headline,
        "user_profile": profile,
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
            "strategic_verdict": verdict_data.get("strategic_verdict") or "",
            "whats_missing": verdict_data.get("whats_missing") or "",
            "recommendation_headline": verdict_data.get("recommendation_headline") or "",
            "user_profile": verdict_data.get("user_profile") or {},
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
            "policy_pincode": stored.get("policy_pincode"),
            "policy_city": stored.get("policy_city"),
            "policy_zone": stored.get("policy_zone"),
            "policy_tier": stored.get("policy_tier"),
            "sum_insured_amount": stored.get("sum_insured_amount"),
            "room_rent_cap": policy.get("room_rent_cap"),
            "room_rent_cap_daily_inr": stored.get("room_rent_cap_daily_inr"),
            "ped_waiting_period_months": policy.get("ped_waiting_period_months"),
            "co_payment_percentage": policy.get("co_payment_percentage"),
            "restoration_benefit": policy.get("restoration_benefit"),
            "consumables_excluded": stored.get("consumables_excluded"),
            "zonal_co_pay": stored.get("zonal_co_pay"),
            **{
                k: stored.get(k)
                for k in ("sub_limits", "maternity_waiting_months", "ambulance_cover", "notes")
            },
        },
        "verdict": policy.get("ai_verdict"),
        "verdict_label": policy.get("verdict_label"),
        "recommendation_summary": meta.get("recommendation_summary") or "",
        "strategic_verdict": meta.get("strategic_verdict") or "",
        "whats_missing": meta.get("whats_missing") or "",
        "recommendation_headline": meta.get("recommendation_headline")
        or _recommendation_headline(str(policy.get("verdict_label") or "REVIEW").upper()),
        "user_profile": meta.get("user_profile") or {},
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
    """Full pipeline: parse PDF → extract metrics → save → geo-aware verdict."""
    extracted_text = extract_audit_pdf_text(file_path)
    if not extracted_text.strip():
        raise ValueError("Could not extract readable text from the PDF.")

    metrics, sources = extract_policy_metrics(extracted_text)
    metrics = enrich_metrics_with_pincode(metrics, extracted_text)

    for key in _METRIC_KEYS:
        if key not in sources:
            fallback = resolve_metric_source(key, metrics.get(key), extracted_text)
            if fallback:
                sources[key] = fallback

    for geo_key in ("policy_pincode", "policy_city", "policy_zone"):
        if geo_key not in sources and metrics.get(geo_key):
            fallback = resolve_metric_source(geo_key, metrics.get(geo_key), extracted_text)
            if fallback:
                sources[geo_key] = fallback

    user_profile = resolve_evaluation_profile(metrics, extracted_text)
    metrics, sources = enrich_metrics_with_context(metrics, extracted_text, user_profile, sources)
    verdict_data = generate_verdict(metrics, user_profile)

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
        "strategic_verdict": verdict_data["strategic_verdict"],
        "whats_missing": verdict_data["whats_missing"],
        "recommendation_headline": verdict_data["recommendation_headline"],
        "user_profile": verdict_data["user_profile"],
        "key_risks": verdict_data["key_risks"],
        "key_strengths": verdict_data["key_strengths"],
        "sources": build_sources_map(policy_row, stored_sources=sources, meta={
            "key_risks": verdict_data["key_risks"],
            "key_strengths": verdict_data["key_strengths"],
            "recommendation_summary": verdict_data["recommendation_summary"],
            "strategic_verdict": verdict_data["strategic_verdict"],
            "whats_missing": verdict_data["whats_missing"],
            "user_profile": verdict_data["user_profile"],
        }),
    }
