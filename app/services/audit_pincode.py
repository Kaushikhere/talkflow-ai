"""Indian pincode resolution for policy geographic evaluation."""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_PINCODE_API = "https://api.postalpincode.in/pincode/{pincode}"

_PINCODE_LABEL_RE = re.compile(
    r"(?:pin\s*code|pincode|postal\s*code|zip\s*code|pin)\s*[:\-]?\s*([1-9]\d{5})\b",
    re.I,
)
_ADDRESS_PIN_RE = re.compile(
    r"(?:address|correspondence|proposer|insured|resident|communication|village|taluk|tehsil|mandal|dist\.?|district)\s*[^\n]{0,160}?\b([1-9]\d{5})\b",
    re.I,
)
_STANDALONE_PIN_RE = re.compile(r"\b([1-9]\d{5})\b")

_ADDRESS_CONTEXT_KEYWORDS = (
    "address",
    "pin",
    "pincode",
    "dist",
    "district",
    "state",
    "village",
    "gram",
    "taluk",
    "tehsil",
    "mandal",
    "correspond",
    "proposer",
    "insured",
    "residen",
    "post",
    "road",
    "nagar",
    "po ",
    "block",
    "tahsil",
)

_DISTRICT_ALIASES: dict[str, str] = {
    "central delhi": "Delhi",
    "east delhi": "Delhi",
    "west delhi": "Delhi",
    "north delhi": "Delhi",
    "south delhi": "Delhi",
    "new delhi": "Delhi",
    "south west delhi": "Delhi",
    "north west delhi": "Delhi",
    "mumbai": "Mumbai",
    "mumbai suburban": "Mumbai",
    "mumbai city": "Mumbai",
    "thane": "Mumbai",
    "bengaluru urban": "Bangalore",
    "bangalore urban": "Bangalore",
    "bengaluru": "Bangalore",
    "bangalore": "Bangalore",
    "chennai": "Chennai",
    "hyderabad": "Hyderabad",
    "kolkata": "Kolkata",
    "ahmedabad": "Ahmedabad",
    "pune": "Pune",
    "gurgaon": "Gurgaon",
    "gurugram": "Gurgaon",
    "jaipur": "Jaipur",
    "lucknow": "Lucknow",
    "indore": "Indore",
    "bhopal": "Bhopal",
    "coimbatore": "Coimbatore",
    "nagpur": "Nagpur",
    "patna": "Patna",
    "kochi": "Kochi",
    "visakhapatnam": "Visakhapatnam",
    "surat": "Surat",
}

_CACHE: dict[str, dict[str, Any]] = {}


def normalize_pincode(value: Any) -> str | None:
    """Return a valid 6-digit Indian pincode string, or None."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) != 6 or digits[0] == "0":
        return None
    return digits


def _has_address_context(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 180): min(len(text), end + 100)].lower()
    return any(keyword in window for keyword in _ADDRESS_CONTEXT_KEYWORDS)


def scan_text_for_pincode(text: str) -> str | None:
    """Find the most likely proposer/resident pincode in policy text."""
    if not text:
        return None

    for pattern in (_PINCODE_LABEL_RE, _ADDRESS_PIN_RE):
        match = pattern.search(text)
        if match:
            pin = normalize_pincode(match.group(1))
            if pin:
                return pin

    for match in _STANDALONE_PIN_RE.finditer(text[:20000]):
        pin = normalize_pincode(match.group(1))
        if pin and _has_address_context(text, match.start(), match.end()):
            return pin
    return None


def _district_to_city(district: str) -> str:
    key = district.strip().lower()
    if key in _DISTRICT_ALIASES:
        return _DISTRICT_ALIASES[key]
    for alias, city in _DISTRICT_ALIASES.items():
        if alias in key or key in alias:
            return city
    return district.strip().title()


def _api_unavailable_fallback(pincode: str) -> dict[str, Any]:
    """Valid pincode format only; location details come from LLM + policy text."""
    return {
        "valid": True,
        "pincode": pincode,
        "city": None,
        "district": None,
        "state": None,
        "post_office": None,
        "branch_type": None,
        "block": None,
        "lookup_source": "pincode_only",
    }


def lookup_india_pincode(pincode: str | int) -> dict[str, Any]:
    """Resolve an Indian pincode to district/city via India Post API (cached)."""
    normalized = normalize_pincode(pincode)
    if not normalized:
        return {"valid": False}

    if normalized in _CACHE:
        return _CACHE[normalized]

    try:
        with httpx.Client(timeout=6.0) as client:
            response = client.get(_PINCODE_API.format(pincode=normalized))
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning("Pincode lookup failed for %s: %s", normalized, exc)
        result = _api_unavailable_fallback(normalized)
        _CACHE[normalized] = result
        return result

    if not isinstance(payload, list) or not payload:
        result = _api_unavailable_fallback(normalized)
        _CACHE[normalized] = result
        return result

    block = payload[0]
    if block.get("Status") != "Success":
        result = _api_unavailable_fallback(normalized)
        _CACHE[normalized] = result
        return result

    offices = block.get("PostOffice") or []
    if not offices:
        result = _api_unavailable_fallback(normalized)
        _CACHE[normalized] = result
        return result

    office = offices[0]
    district = str(office.get("District") or "").strip()
    state = str(office.get("State") or "").strip()
    branch_type = str(office.get("BranchType") or "").strip() or None
    block_name = str(office.get("Block") or office.get("Taluk") or "").strip() or None
    city = _district_to_city(district) if district else None

    result = {
        "valid": True,
        "pincode": normalized,
        "city": city,
        "district": district or city,
        "state": state or None,
        "post_office": str(office.get("Name") or "").strip() or None,
        "branch_type": branch_type,
        "block": block_name,
        "lookup_source": "india_post_api",
    }
    _CACHE[normalized] = result
    return result


def enrich_metrics_with_pincode(metrics: dict[str, Any], policy_text: str) -> dict[str, Any]:
    """Ensure policy_pincode is set from extraction or regex scan."""
    pin = normalize_pincode(metrics.get("policy_pincode"))
    if not pin:
        pin = scan_text_for_pincode(policy_text)
    if pin:
        metrics = {**metrics, "policy_pincode": pin}
    return metrics
