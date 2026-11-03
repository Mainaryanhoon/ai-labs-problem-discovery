"""Approved source roles for demand / capability eligibility.

Roles mirror ``docs/tatvic/SOURCES.md``. The model label ``KEEP_DEMAND`` must
never override these roles at the scoring layer.
"""

from __future__ import annotations

from typing import Literal

SourceRole = Literal[
    "demand",
    "capability",
    "capability_corroboration",
    "context",
    "company_trigger",
    "india_evidence",
    "buying_evidence",
    "buyer_route",
    "market",
    "idea",
    "opt_in_corroboration",
    "opt_in_context",
    "unknown",
]

EVIDENCE_ROLES = frozenset({
    "demand",
    "capability",
    "context",
    "company_trigger",
    "india_evidence",
    "buying_evidence",
    "buyer_route",
    "market",
    "idea",
})

MARKET_LEAD_ROLES = frozenset({"market", "idea"})

def is_valid_evidence_role(role: object) -> bool:
    return isinstance(role, str) and role in EVIDENCE_ROLES


def permitted_item_roles(
    source_name: str | None,
    policy_url: str | None = None,
) -> frozenset[str]:
    source_key = normalize_source_key(source_name)
    base = _policy_item_roles(source_name, source_key)
    if not base:
        return frozenset()
    if not policy_url:
        return base
    from app.source_policy import policy_roles_for_url

    configured = policy_roles_for_url(policy_url)
    if configured is not None:
        return configured
    if source_key in {"articles", "article", "google_developer_forums", "google developer forums"}:
        return frozenset({"context"})
    return base


def _policy_item_roles(
    source_name: str | None,
    source_key: str,
) -> frozenset[str]:
    from app.source_policy import policy_roles_for_source

    roles = policy_roles_for_source(source_name)
    if roles:
        if "capability_corroboration" in roles:
            return frozenset({"context"})
        return roles & EVIDENCE_ROLES
    if source_key in {"articles", "article", "google_developer_forums", "google developer forums"}:
        return frozenset({"context"})
    return frozenset()


def normalize_source_key(source_name: str | None) -> str:
    raw = (source_name or "").strip()
    if not raw:
        return ""
    # "Stack Exchange:stackoverflow" → stack exchange family
    lower = raw.casefold()
    if lower.startswith("stack exchange"):
        return "stack exchange"
    if lower.startswith("reddit"):
        return "reddit"
    return lower.replace("-", "_")


def source_role(source_name: str | None) -> SourceRole:
    """Return the approved role for a stored or pipeline source identity."""
    key = normalize_source_key(source_name)
    if not key:
        return "unknown"
    if key in {"google developer forums", "google_developer_forums"}:
        from app.source_policy import policy_forum_feeds, policy_roles_for_url

        if any(
            "demand" in (policy_roles_for_url(url) or frozenset())
            for url in policy_forum_feeds()
        ):
            return "demand"
    from app.source_policy import policy_roles_for_source

    roles = policy_roles_for_source(source_name)
    if "demand" in roles:
        return "demand"
    if "capability_corroboration" in roles:
        return "capability_corroboration"
    if key == "youtube" and roles & {"capability", "context"}:
        return "opt_in_context"
    if "capability" in roles:
        return "capability"
    if "idea" in roles:
        return "idea"
    if "market" in roles:
        return "market"
    if "context" in roles:
        return "context"
    return "unknown"


def is_demand_role(source_name: str | None) -> bool:
    """Only demand-role sources may contribute to the demand evidence floor."""
    return source_role(source_name) == "demand"


def is_market_lead_role(
    source_name: str | None,
    policy_url: str | None = None,
) -> bool:
    """Market/idea sources may contribute to the evidence floor without Tatvic-section demand."""
    if source_role(source_name) in MARKET_LEAD_ROLES:
        return True
    return bool(permitted_item_roles(source_name, policy_url) & MARKET_LEAD_ROLES)


def build_role_claim(
    source_name: str | None,
    role: str,
    *,
    source_text: str,
    evidence_quote: str,
    justification: str,
    policy_url: str | None = None,
) -> dict[str, str] | None:
    """Validate one role against source policy and an exact stored-text quote."""
    source_key = normalize_source_key(source_name)
    allowed = permitted_item_roles(source_name, policy_url)
    if (
        not is_valid_evidence_role(role)
        or role not in allowed
        or not evidence_quote.strip()
        or evidence_quote not in source_text
        or not justification.strip()
    ):
        return None
    if role == "demand" and not is_demand_role(source_name) and not policy_url:
        return None
    return {
        "role": role,
        "evidence_quote": evidence_quote,
        "justification": justification.strip(),
    }


def is_capability_or_context_role(source_name: str | None) -> bool:
    return source_role(source_name) in {
        "capability",
        "capability_corroboration",
        "context",
        "opt_in_corroboration",
        "opt_in_context",
    }


def role_diagnostics(
    source_name: str | None,
    policy_url: str | None = None,
) -> dict[str, str | bool]:
    role = source_role(source_name)
    market_lead = is_market_lead_role(source_name, policy_url)
    return {
        "source_name": (source_name or "").strip() or "—",
        "source_role": role,
        "counts_toward_demand_floor": role == "demand" or market_lead,
        "mapping_known": role != "unknown" or market_lead,
    }
