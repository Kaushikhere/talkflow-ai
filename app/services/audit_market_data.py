"""LLM-driven Indian health insurance market benchmarks from policy geography."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import AUDIT_ANALYSIS_MODEL, AUDIT_CONTEXT_CHARS, AUDIT_MAX_TOKENS_GEO
from app.services.audit_pincode import lookup_india_pincode, normalize_pincode
from app.services.groq_client import get_groq_client, groq_assistant_text

logger = logging.getLogger(__name__)

_GEO_SCHEMA = """{
  "policy_city": "Primary city or town for this policyholder (string)",
  "city_tier": "Tier 1 | Tier 2 | Tier 3",
  "locality_type": "metro | city | town | village",
  "local_room_cost": 8000,
  "min_sum_insured_benchmark": 1000000,
  "typical_hospitals_context": "One sentence on typical private hospitals and room economics here",
  "resolution_note": "Brief explanation of how you determined this location and tier"
}"""

_TIER_LABELS: dict[str, str] = {
    "Tier 1": "Tier 1 Metro",
    "Tier 2": "Tier 2 City",
    "Tier 3": "Tier 3 Town / Village",
}

_UNKNOWN_VALUES = frozenset({"", "unknown", "not mentioned", "not stated", "n/a", "na", "null", "none"})

_MARKET_SOURCES = [
    "Room rent proportionate-deduction: sub-limit on room rent reduces the entire hospital bill proportionally",
    "Non-medical consumables (PPE, gloves, syringes) are commonly excluded unless a Safeguard/Consumables rider is bought",
    "Zonal pricing: treatment in a higher-cost zone than purchase zone often attracts 10–20% co-pay",
]


def _is_unknown(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in _UNKNOWN_VALUES


def _normalize_tier(value: Any) -> str | None:
    if _is_unknown(value):
        return None
    text = str(value).strip()
    if text in _TIER_LABELS:
        return text
    match = re.search(r"tier\s*([123])", text, re.I)
    if match:
        return f"Tier {match.group(1)}"
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
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


def _parse_json_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def _pincode_facts_block(pin_info: dict[str, Any] | None) -> str:
    if not pin_info or not pin_info.get("valid"):
        return "- India Post lookup: not available\n"
    lines = [
        f"- Pincode: {pin_info.get('pincode')}",
        f"- District: {pin_info.get('district') or 'unknown'}",
        f"- State: {pin_info.get('state') or 'unknown'}",
        f"- Post office: {pin_info.get('post_office') or 'unknown'}",
        f"- Branch type: {pin_info.get('branch_type') or 'unknown'}",
        f"- Block/Taluk: {pin_info.get('block') or 'unknown'}",
    ]
    return "\n".join(lines) + "\n"


def _build_geo_prompt(metrics: dict[str, Any], policy_text: str, pin_info: dict[str, Any] | None) -> str:
    metrics_geo = {
        key: metrics.get(key)
        for key in (
            "policy_pincode",
            "policy_city",
            "policy_zone",
            "policy_tier",
            "sum_insured_amount",
            "zonal_co_pay",
        )
    }
    snippet = policy_text[: min(len(policy_text), AUDIT_CONTEXT_CHARS)]
    return (
        "You are an Indian health insurance market analyst. Determine the realistic local healthcare "
        "economics for the policyholder location using:\n"
        "1) Fields extracted from the policy document\n"
        "2) India Post pincode facts (if provided)\n"
        "3) Address / zone / co-pay clauses in the policy excerpt\n\n"
        "Use your knowledge of Indian cities, towns, villages, pincodes, and typical private-hospital "
        "room rents (2024–2025). A village branch post office in a district is Tier 3, not metro pricing. "
        "Do not default to Mumbai/Delhi unless the policy location is actually there.\n\n"
        "### EXTRACTED POLICY GEO FIELDS ###\n"
        f"{json.dumps(metrics_geo, indent=2)}\n\n"
        "### INDIA POST PINCODE FACTS ###\n"
        f"{_pincode_facts_block(pin_info)}"
        "### POLICY EXCERPT (addresses, zones, co-pay) ###\n"
        f"{snippet}\n\n"
        "Reply with ONLY valid JSON matching this schema (no markdown):\n"
        f"{_GEO_SCHEMA}\n"
    )


def _assemble_profile(
    geo: dict[str, Any],
    metrics: dict[str, Any],
    pin_info: dict[str, Any] | None,
    *,
    city_source: str,
) -> dict[str, Any]:
    pin = normalize_pincode(metrics.get("policy_pincode")) or (
        pin_info.get("pincode") if pin_info and pin_info.get("valid") else None
    )
    tier = _normalize_tier(geo.get("city_tier")) or _normalize_tier(metrics.get("policy_tier")) or "Tier 2"
    policy_city = str(geo.get("policy_city") or "").strip()
    if _is_unknown(policy_city):
        policy_city = None
    if not policy_city and not _is_unknown(metrics.get("policy_city")):
        policy_city = str(metrics.get("policy_city")).strip()
    if not policy_city and pin_info and pin_info.get("valid"):
        policy_city = pin_info.get("district") or pin_info.get("post_office")

    local_room = _coerce_int(geo.get("local_room_cost"))
    min_si = _coerce_int(geo.get("min_sum_insured_benchmark"))
    policy_si = _coerce_int(metrics.get("sum_insured_amount"))
    effective_si = policy_si if policy_si else (min_si or 500_000)

    if local_room is None or local_room < 500:
        local_room = max(int(effective_si * 0.01), 1500) if effective_si else 2500
    if min_si is None or min_si < 100_000:
        min_si = effective_si

    locality = str(geo.get("locality_type") or "town").strip().lower()
    if locality not in ("metro", "city", "town", "village"):
        locality = "town"

    policy_zone = metrics.get("policy_zone")
    display_city = policy_city or "Not stated in policy"

    return {
        "policy_pincode": pin,
        "policy_state": pin_info.get("state") if pin_info and pin_info.get("valid") else None,
        "policy_district": pin_info.get("district") if pin_info and pin_info.get("valid") else None,
        "policy_post_office": pin_info.get("post_office") if pin_info and pin_info.get("valid") else None,
        "pincode_lookup_source": pin_info.get("lookup_source") if pin_info and pin_info.get("valid") else None,
        "locality_type": locality,
        "policy_city": policy_city,
        "user_city": display_city,
        "city_tier": tier,
        "city_tier_label": _TIER_LABELS.get(tier, tier),
        "local_room_cost": int(local_room),
        "user_sum_insured": int(effective_si),
        "min_sum_insured_benchmark": int(min_si),
        "typical_hospitals_context": str(geo.get("typical_hospitals_context") or "").strip(),
        "market_sources": list(_MARKET_SOURCES),
        "policy_zone": None if _is_unknown(policy_zone) else str(policy_zone).strip(),
        "city_source": city_source,
        "city_resolution_note": str(geo.get("resolution_note") or "").strip()
        or "Location and benchmarks resolved by geographic analysis model.",
    }


def _resolve_geo_with_llm(
    metrics: dict[str, Any],
    policy_text: str,
    pin_info: dict[str, Any] | None,
) -> dict[str, Any] | None:
    prompt = _build_geo_prompt(metrics, policy_text, pin_info)
    client = get_groq_client()
    raw = ""
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=AUDIT_ANALYSIS_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=AUDIT_MAX_TOKENS_GEO,
            )
            raw = groq_assistant_text(response.choices[0].message) or ""
            geo = _parse_json_response(raw)
            return _assemble_profile(geo, metrics, pin_info, city_source="llm_geo_analysis")
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            if attempt == 0:
                logger.warning("Geo profile JSON parse failed, retrying: %s", exc)
                prompt = (
                    "Fix the following into valid JSON matching the schema exactly. "
                    "Output ONLY JSON:\n"
                    f"{_GEO_SCHEMA}\n\nBroken output:\n{raw[:1500]}"
                )
                continue
            logger.warning("Geo profile parse failed after retry: %s", exc)
        except Exception as exc:
            logger.error("Geo profile LLM call failed: %s", exc)
            break
    return None


def _fallback_profile(metrics: dict[str, Any], pin_info: dict[str, Any] | None) -> dict[str, Any]:
    """Minimal profile when LLM geo analysis is unavailable."""
    tier = _normalize_tier(metrics.get("policy_tier")) or "Tier 2"
    policy_si = _coerce_int(metrics.get("sum_insured_amount"))
    geo = {
        "policy_city": metrics.get("policy_city"),
        "city_tier": tier,
        "locality_type": "town",
        "local_room_cost": max(int((policy_si or 500_000) * 0.01), 1500),
        "min_sum_insured_benchmark": policy_si or 500_000,
        "typical_hospitals_context": "",
        "resolution_note": (
            "Geographic analysis model unavailable; using policy-stated location and "
            "1% sum-insured room-rent heuristic only."
        ),
    }
    return _assemble_profile(geo, metrics, pin_info, city_source="policy_fallback")


def resolve_evaluation_profile(
    metrics: dict[str, Any],
    policy_text: str = "",
) -> dict[str, Any]:
    """Build verdict geography via LLM analysis of pincode, policy text, and India Post facts."""
    pin = normalize_pincode(metrics.get("policy_pincode"))
    pin_info = lookup_india_pincode(pin) if pin else None

    profile = _resolve_geo_with_llm(metrics, policy_text, pin_info)
    if profile:
        return profile

    logger.warning("Falling back to minimal geo profile without LLM benchmarks")
    return _fallback_profile(metrics, pin_info)


def build_market_context_block(profile: dict[str, Any]) -> str:
    """Market benchmark block injected into the underwriter prompt."""
    city_line = profile.get("policy_city") or profile.get("user_city") or "Not stated in policy"
    note = profile.get("city_resolution_note")
    hospitals = profile.get("typical_hospitals_context")
    pin = profile.get("policy_pincode")
    lines = [
        "### LIVE MARKET BENCHMARKS (India, LLM-resolved for this policy location) ###",
    ]
    if pin:
        loc = profile.get("policy_district") or city_line
        state = profile.get("policy_state")
        loc_line = f"{loc}, {state}" if state else str(loc)
        lines.append(f"- Policy pincode (from document): {pin} → {loc_line}")
    lines.extend([
        f"- Policy city (resolved): {city_line} ({profile['city_tier_label']})",
        f"- Locality type: {profile.get('locality_type') or 'unknown'}",
        f"- Policy zone (from document): {profile.get('policy_zone') or 'Not stated'}",
        f"- Expected average local private hospital room cost: ₹{profile['local_room_cost']:,}/day",
        f"- Policy sum insured (extracted): ₹{profile['user_sum_insured']:,}",
        f"- Minimum recommended sum insured for {profile['city_tier']}: "
        f"₹{profile['min_sum_insured_benchmark']:,}",
        "- Room rent exceeding policy cap triggers proportionate deduction on the ENTIRE bill, not just room excess",
        "- Non-medical consumables (PPE, gloves, syringes) are commonly excluded unless a Safeguard/Consumables rider is bought",
        "- Zonal pricing: treatment in a higher-cost zone than purchase zone often attracts 10–20% co-pay",
        "Sources: " + "; ".join(profile.get("market_sources") or _MARKET_SOURCES),
    ])
    if hospitals:
        lines.insert(-1, f"- Local hospital context: {hospitals}")
    if note:
        lines.insert(2 if not pin else 3, f"- Resolution note: {note}")
    return "\n".join(lines)
