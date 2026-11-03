"""Coerce messy LLM JSON into shapes ProblemExtraction accepts."""

from __future__ import annotations

import json
import re
from typing import Any

_WTP = frozenset({"explicit", "suggested", "none", "unclear"})
_SIGNAL = frozenset({
    "pain_point",
    "explicit_tool_request",
    "workaround_or_manual_work",
    "feature_gap",
    "buying_signal",
    "general_discussion",
    "noise",
})
_ITEM_KIND = frozenset({"demand", "capability"})
_BUILDABILITY = frozenset({"likely", "unclear", "unlikely"})
_DELIVERY = frozenset({"service", "product", "unclear"})
_DELIVERY_ALIASES = {
    "standalone": "product",
    "standalone product": "product",
    "standalone_product": "product",
    "software": "product",
    "saas": "product",
    "service engagement": "service",
    "professional service": "service",
    "consulting": "service",
    "unknown": "unclear",
    "n/a": "unclear",
}
_SECTION = frozenset({
    "Analytics",
    "Cloud and Data Engineering",
    "Data Science and AI/ML",
    "Data Science and AI",
    "Agentic AI",
    "CRO",
    "OUT_OF_SCOPE",
})
_SECTION_ALIASES = {
    "data_science_and_ai": "Data Science and AI/ML",
    "data_science_and_ai_ml": "Data Science and AI/ML",
    "agentic_ai": "Agentic AI",
    "agentic": "Agentic AI",
}
_FILTER = frozenset({
    "KEEP_DEMAND",
    "KEEP_CAPABILITY",
    "N1_IDEA_CONTEXT",
    "SUPPORTING_ONLY",
    "REVIEW",
    "DROP_SPAM",
    "DROP_HYGIENE",
    "DROP_OUT_OF_SCOPE_TOPIC",
    "DROP_CONSUMER",
    "DROP_NO_PROBLEM",
    "DROP_NOT_PRACTITIONER",
    "DROP_NOT_SOLVABLE",
    "DROP_NATIVE_SOLVED",
    "DROP_REGULATORY",
    "DROP_LOW_EVIDENCE",
    "DROP_DUPLICATE",
})
_CATEGORY = frozenset({
    "Business Operations",
    "Sales & Marketing",
    "Finance & Accounting",
    "Customer Support",
    "Human Resources",
    "Productivity",
    "Software & IT",
    "Healthcare",
    "Education",
    "Other",
})

_NULLISH = frozenset({"", "none", "null", "n/a", "na", "not applicable", "n.a."})

_ITEM_KIND_ALIASES = {
    "tutorial": "demand",
    "content": "demand",
    "howto": "demand",
    "how-to": "demand",
    "capability_signal": "capability",
    "demand_signal": "demand",
}

_BUILDABILITY_ALIASES = {
    "not applicable": "unclear",
    "not_applicable": "unclear",
    "n/a": "unclear",
    "na": "unclear",
    "unknown": "unclear",
    "yes": "likely",
    "no": "unlikely",
    "possible": "likely",
    "maybe": "unclear",
}

_CATEGORY_ALIASES = {
    "general": "Other",
    "technology": "Software & IT",
    "tech": "Software & IT",
    "it": "Software & IT",
    "software": "Software & IT",
    "marketing": "Sales & Marketing",
    "sales": "Sales & Marketing",
    "operations": "Business Operations",
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _is_nullish(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().casefold() in _NULLISH:
        return True
    return False


def _coerce_str(value: Any, default: str = "Unclear from submission") -> str:
    if _is_nullish(value):
        return default
    if isinstance(value, str):
        return value.strip() or default
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return default


def _coerce_str_list(value: Any, *, max_items: int | None = None) -> list[str]:
    if _is_nullish(value):
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if ";" in text and len(text) > 80:
            parts = [p.strip() for p in text.split(";") if p.strip()]
            items = parts if len(parts) > 1 else [text]
        else:
            items = [text]
    elif isinstance(value, list):
        items = []
        for item in value:
            if _is_nullish(item):
                continue
            items.append(_coerce_str(item, default=""))
        items = [item for item in items if item]
    else:
        items = [_coerce_str(value)]
    if max_items is not None:
        return items[:max_items]
    return items


def _pick_literal(
    value: Any,
    allowed: frozenset[str],
    *,
    default: str,
    aliases: dict[str, str] | None = None,
) -> str:
    if isinstance(value, str):
        lowered = value.strip().casefold()
        for candidate in allowed:
            if candidate.casefold() == lowered:
                return candidate
    if _is_nullish(value):
        return default
    if not isinstance(value, str):
        value = str(value)
    raw = value.strip()
    if raw in allowed:
        return raw
    key = raw.casefold().replace("-", "_").replace(" ", "_")
    if aliases and key in aliases:
        mapped = aliases[key]
        return mapped if mapped in allowed else default
    slug = _slug(raw)
    for candidate in allowed:
        if _slug(candidate) == slug:
            return candidate
    if aliases:
        for alias, mapped in aliases.items():
            if alias == key or alias == slug:
                return mapped if mapped in allowed else default
    return default


def _coerce_int_1_to_5(value: Any, default: int = 3) -> int:
    """Coerce required 1–5 ratings (urgency/frequency/evidence) with a fallback.

    Do not use this for optional Tatvic qualitative components — those must use
    `_optional_component_score_1_to_5` so malformed values stay missing.
    """
    if _is_nullish(value):
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(1, min(5, number))


def _optional_component_score_1_to_5(value: Any) -> int | None:
    """Parse optional qualitative 1–5 scores without inventing mid-scale defaults.

    Valid integers in 1..5 are kept. Missing, nullish, bool, non-numeric,
    non-integer, or out-of-range values become ``None`` so scoring can apply
    RUBRIC/MASTER ``1 + "no evidence"``.
    """
    if _is_nullish(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 5 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        number = int(value)
        return number if 1 <= number <= 5 else None
    if isinstance(value, str):
        text = value.strip()
        if not text or _is_nullish(text):
            return None
        try:
            as_float = float(text)
        except ValueError:
            return None
        if not as_float.is_integer():
            return None
        number = int(as_float)
        return number if 1 <= number <= 5 else None
    return None


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if _is_nullish(value):
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return default


def normalize_extraction_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``raw`` with types and enums aligned to ProblemExtraction."""
    if not isinstance(raw, dict):
        raise TypeError("extraction payload must be a dict")

    data = dict(raw)

    data["is_genuine_problem"] = _coerce_bool(
        data.get("is_genuine_problem"),
        default=False,
    )
    data["signal_type"] = _pick_literal(
        data.get("signal_type"),
        _SIGNAL,
        default="general_discussion",
    )
    data["willingness_to_pay_signal"] = _pick_literal(
        data.get("willingness_to_pay_signal"),
        _WTP,
        default="unclear",
        aliases={"none": "none", "null": "none"},
    )
    data["item_kind"] = _pick_literal(
        data.get("item_kind"),
        _ITEM_KIND,
        default="demand",
        aliases=_ITEM_KIND_ALIASES,
    )
    data["software_buildability"] = _pick_literal(
        data.get("software_buildability"),
        _BUILDABILITY,
        default="unclear",
        aliases=_BUILDABILITY_ALIASES,
    )
    data["delivery_type"] = _pick_literal(
        data.get("delivery_type"),
        _DELIVERY,
        default="unclear",
        aliases=_DELIVERY_ALIASES,
    )
    if data.get("section") is not None and not _is_nullish(data.get("section")):
        data["section"] = _pick_literal(
            data.get("section"),
            _SECTION,
            default="OUT_OF_SCOPE",
            aliases=_SECTION_ALIASES,
        )
        if data["section"] == "Data Science and AI":
            data["section"] = "Data Science and AI/ML"
    if data.get("filter_reason_code") is not None and not _is_nullish(
        data.get("filter_reason_code")
    ):
        data["filter_reason_code"] = _pick_literal(
            data.get("filter_reason_code"),
            _FILTER,
            default="REVIEW",
        )
    data["category"] = _pick_literal(
        data.get("category"),
        _CATEGORY,
        default="Other",
        aliases={_slug(k): v for k, v in _CATEGORY_ALIASES.items()},
    )

    data["problem_statement"] = _coerce_str(
        data.get("problem_statement"),
        default="No problem statement provided.",
    )
    data["affected_user"] = _coerce_str(data.get("affected_user"))
    data["context"] = _coerce_str(
        data.get("context"),
        default="Context was not specified in the submission.",
    )
    data["industry"] = _coerce_str(data.get("industry"), default="Unclear")

    for optional in (
        "current_workaround",
        "solution_request",
        "noise_reason",
        "rejection_reason",
        "filter_reason_sentence",
        "smallest_first_version",
        "first_client_type",
        "why_now",
        "first_customer_reach_strategy",
        "failure_risk_line",
        "software_buildability_reason",
        "vertical",
    ):
        if optional in data and _is_nullish(data[optional]):
            data[optional] = None
        elif optional in data and data[optional] is not None:
            data[optional] = _coerce_str(data[optional], default="")

    data["evidence_quotes"] = _coerce_str_list(
        data.get("evidence_quotes"),
        max_items=3,
    )
    data["component_evidence"] = _coerce_str_list(
        data.get("component_evidence"),
        max_items=8,
    )
    data["uncertainty_notes"] = _coerce_str_list(data.get("uncertainty_notes"))
    data["tags"] = _coerce_str_list(data.get("tags"))

    for score_key in (
        "urgency_1_to_5",
        "frequency_signal_1_to_5",
        "evidence_strength_1_to_5",
    ):
        data[score_key] = _coerce_int_1_to_5(data.get(score_key))

    for optional_score in (
        "capability_readiness_1_to_5",
        "section_client_fit_1_to_5",
        "leverage_on_offerings_1_to_5",
        "commercial_signal_1_to_5",
        "effort_to_first_version_1_to_5",
        "defensibility_1_to_5",
    ):
        if optional_score in data:
            data[optional_score] = _optional_component_score_1_to_5(
                data.get(optional_score)
            )

    if not data["is_genuine_problem"] and not data.get("rejection_reason"):
        data["rejection_reason"] = (
            "The submission does not describe an actionable practitioner problem."
        )
    if data["signal_type"] == "noise" and not data.get("noise_reason"):
        data["noise_reason"] = "Classified as noise by the extraction model."

    if not data.get("software_buildability_reason"):
        data["software_buildability_reason"] = (
            "The submission does not provide enough information to assess this."
        )

    return data
