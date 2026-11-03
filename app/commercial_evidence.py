"""Validate model-proposed evidence claims against policy and stored text."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.source_policy import host_allowed
from app.source_roles import build_role_claim, is_valid_evidence_role
from app.models import SourceObservation, Submission

_INDIA_RE = re.compile(
    r"\b(india|indian|mumbai|bengaluru|bangalore|delhi|hyderabad|"
    r"chennai|pune|kolkata|gurugram|gurgaon|noida)\b|₹|\binr\b|\bgstin\b",
    re.IGNORECASE,
)
_INDIA_OPERATIONS_RE = re.compile(
    r"\b(office|operat(?:es|ions|ing)|headquarter(?:ed|s)?|"
    r"subsidiar(?:y|ies)|registered|based|employees|team|facility|"
    r"presence|launched|expanded)\b.{0,100}\b(india|indian|"
    r"mumbai|bengaluru|bangalore|delhi|hyderabad|chennai|pune|"
    r"kolkata|gurugram|gurgaon|noida)\b|"
    r"\b(india|indian|mumbai|bengaluru|bangalore|delhi|hyderabad|"
    r"chennai|pune|kolkata|gurugram|gurgaon|noida)\b.{0,100}"
    r"\b(office|operat(?:es|ions|ing)|headquarter(?:ed|s)?|"
    r"subsidiar(?:y|ies)|registered|based|employees|team|facility|presence)\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"\b(?:19|20)\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-](?:\d{2}|\d{4})\b|"
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+\d{1,2}(?:,?\s+(?:19|20)\d{2})?\b",
    re.IGNORECASE,
)
_TRIGGER_TERMS = {
    "launch": re.compile(r"\b(launch(?:ed|es|ing)?|introduc(?:ed|es|ing))\b", re.I),
    "expansion": re.compile(r"\b(expan(?:d|ded|sion)|enter(?:ed|s|ing))\b", re.I),
    "funding": re.compile(r"\b(rais(?:e|es|ed|ing)|fund(?:ing|ed)|series\s+[a-f])\b", re.I),
    "acquisition": re.compile(r"\b(acquir(?:e|es|ed|ing)|acquisition|merg(?:e|es|ed|er))\b", re.I),
    "transformation": re.compile(r"\b(transform(?:ation|ed|s|ing)?|moderni[sz](?:e|es|ed|ing))\b", re.I),
    "strategic_shift": re.compile(r"\b(strategic shift|pivot(?:ed|s|ing)?|restructur(?:e|ed|ing))\b", re.I),
}
_BUYING_RE = re.compile(
    r"\b(rfp|rfq|tender|procurement|request for proposal|request for quote|"
    r"looking for (?:a |an )?(?:vendor|provider|agency|partner)|"
    r"seeking (?:a |an )?(?:vendor|provider|agency|partner)|"
    r"invite(?:s|d)? (?:vendors|providers|agencies)|"
    r"hire an? (?:agency|implementation partner|service provider))\b",
    re.IGNORECASE,
)
_PROBLEM_RE = re.compile(
    r"\b(unable to|cannot|can't|doesn't work|not working|failing|fails|"
    r"error|issue|problem|struggling|need help|how do i|how can i|"
    r"not sure how|keeps? (?:breaking|failing)|don't match|do not match|"
    r"mismatch(?:es|ed|ing)?)\b",
    re.IGNORECASE,
)
_ANNOUNCEMENT_RE = re.compile(
    r"\b(announc(?:e|es|ed|ing)|launch(?:ed|es|ing)|webinar|tutorial|"
    r"now available|release notes|press release|case study|hiring)\b",
    re.IGNORECASE,
)
_ANSWER_RE = re.compile(
    r"\b(accepted answer|marked as solution|here(?:'s| is) the solution|"
    r"the answer is|solved by|this fixes it)\b",
    re.IGNORECASE,
)
_PUBLICATION_CONTACT_RE = re.compile(
    r"\b(contact|sales|procurement|business inquiries|vendor inquiries|"
    r"partnership inquiries)\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PUBLIC_MAILBOX_RE = re.compile(
    r"(?:^|[-._])(contact|info|sales|procurement|vendor|partner|"
    r"business|partnership|inquiries)(?:$|[-._])",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def canonical_company_name(value: str | None) -> str | None:
    """Normalize exact company mentions only; never fuzzy-match or remove suffixes."""
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = unicodedata.normalize("NFKC", value).casefold()
    canonical = " ".join(re.findall(r"[\w]+", normalized, flags=re.UNICODE))
    return canonical or None


_GENERIC_COMPANY_TERMS: frozenset[str] = frozenset({
    "company",
    "client",
    "brand",
    "startup",
    "retailer",
    "vendor",
    "partner",
    "agency",
    "firm",
    "team",
    "holding",
    "holdings",
    "group",
    "enterprise",
    "organization",
    "organisation",
    "business",
})


def link_company_evidence(
    claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Link claims only when deterministic identifiers support the match.

    Never fuzzy-matches names. Generic terms and single-item mentions remain
    unresolved with explicit reasons.
    """
    item_ids_by_name: dict[str, set[str]] = {}
    for claim in claims:
        key = canonical_company_name(claim.get("company_name"))
        submission_id = claim.get("submission_id")
        canonical_url = claim.get("canonical_url") or claim.get("source_url")
        if key and isinstance(submission_id, int):
            source_item_key = (
                str(canonical_url).casefold()
                if isinstance(canonical_url, str) and canonical_url.strip()
                else str(claim.get("source_item_id") or submission_id)
            )
            item_ids_by_name.setdefault(key, set()).add(source_item_key)
    linked = []
    for claim in claims:
        key = canonical_company_name(claim.get("company_name"))
        is_ambiguous = bool(claim.get("is_ambiguous") or claim.get("ambiguous"))
        is_generic = bool(key in _GENERIC_COMPANY_TERMS) if key else False
        item_count = len(item_ids_by_name.get(key, set())) if key else 0

        if is_generic:
            resolution = "unresolved"
            reason = (
                "Generic descriptor cannot be deterministically resolved to a "
                "specific company identity."
            )
        elif is_ambiguous:
            resolution = "unresolved"
            reason = (
                "Ambiguous company identity: multiple potential entities or "
                "insufficient distinguishing context."
            )
        elif item_count >= 2:
            resolution = "canonical_exact_name"
            reason = (
                "Exact canonical name repeats across source items; no official "
                "domain or legal-entity identity is inferred."
            )
        else:
            resolution = "unresolved"
            reason = "Only one source item supports this company mention."

        linked.append({
            **claim,
            "company_identity_key": key,
            "company_identity_resolution": resolution,
            "company_identity_source_item_count": item_count,
            "company_identity_resolution_reason": reason,
        })
    return linked


def _date_iso(value: datetime | str | None) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _event_date_iso(value: str | None) -> str | None:
    if not value:
        return None
    for date_format in (
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(value.strip(), date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _route_is_public_and_purposeful(route: str, quote: str) -> bool:
    if route not in quote:
        return False
    if _EMAIL_RE.fullmatch(route):
        mailbox = route.partition("@")[0]
        return bool(
            _PUBLICATION_CONTACT_RE.search(quote)
            and _PUBLIC_MAILBOX_RE.search(mailbox)
        )
    try:
        parsed = urlparse(route)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and host_allowed(route)
        and bool(re.search(r"contact|sales|procurement|vendor|partner", parsed.path, re.I))
    )


def _validate_role_specific(
    proposal: dict[str, Any],
    *,
    role: str,
    text: str,
    quote: str,
    source_name: str,
    evidence_context: dict[str, Any],
    analysis_classification: dict[str, Any],
) -> str | None:
    company = _text(proposal.get("company_name"))
    if role in {"company_trigger", "india_evidence", "buying_evidence", "buyer_route"}:
        if not company or company not in quote:
            return "company_name_not_supported_by_quote"

    if role == "company_trigger":
        trigger_type = proposal.get("trigger_type")
        event_date = _text(proposal.get("event_date_quote"))
        if trigger_type not in _TRIGGER_TERMS:
            return "trigger_type_missing_or_invalid"
        if not _TRIGGER_TERMS[trigger_type].search(quote):
            return "trigger_type_not_supported_by_quote"
        if not event_date or event_date not in quote or not _DATE_RE.search(event_date):
            return "explicit_event_date_missing"
    elif role == "india_evidence":
        if not _INDIA_RE.search(quote):
            return "explicit_india_evidence_missing"
    elif role == "buying_evidence":
        if not _BUYING_RE.search(quote):
            return "explicit_procurement_or_provider_search_missing"
    elif role == "buyer_route":
        route = _text(proposal.get("route"))
        if not route or not _route_is_public_and_purposeful(route, quote):
            return "public_purposeful_route_not_verified"
    elif role == "demand":
        if (
            analysis_classification.get("is_genuine_problem") is not True
            or analysis_classification.get("signal_type") not in {
                "pain_point",
                "explicit_tool_request",
                "workaround_or_manual_work",
                "feature_gap",
            }
            or analysis_classification.get("filter_reason_code") != "KEEP_DEMAND"
        ):
            return "problem_analysis_does_not_authorize_demand"
        if not _PROBLEM_RE.search(quote):
            return "genuine_problem_statement_not_supported"
        if _ANNOUNCEMENT_RE.search(quote):
            return "announcement_or_promotion_not_demand"
        if _ANSWER_RE.search(quote):
            return "answer_or_solution_not_demand"

    if source_name.casefold() == "youtube":
        evidence_source = proposal.get("evidence_source")
        source_text = {
            "title": evidence_context.get("title"),
            "description": evidence_context.get("description"),
            "transcript": evidence_context.get("transcript_text"),
        }.get(evidence_source)
        if not isinstance(source_text, str) or quote not in source_text:
            return "video_quote_source_unavailable_or_mismatched"

    if company and company not in text:
        return "company_name_not_in_stored_text"
    return None


def validate_evidence_claims(
    proposals: list[Any],
    *,
    source_name: str,
    source_text: str,
    policy_url: str | None,
    source_item_id: int,
    submission_id: int,
    source_url: str,
    canonical_url: str,
    publication_date: datetime | str | None,
    evidence_context: dict[str, Any] | None = None,
    analysis_classification: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return accepted claims and compact diagnostics; never trusts model IDs."""
    claims: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    duplicates = 0
    seen: set[tuple[str, str, str, str, str]] = set()
    evidence_context = evidence_context or {}
    analysis_classification = analysis_classification or {}

    for index, raw in enumerate(proposals):
        proposal = (
            raw
            if isinstance(raw, dict)
            else raw.model_dump()
            if hasattr(raw, "model_dump")
            else None
        )
        if not isinstance(proposal, dict):
            rejected.append({"index": str(index), "reason": "malformed_claim_shape"})
            continue
        role = proposal.get("role")
        if not is_valid_evidence_role(role):
            rejected.append({"index": str(index), "reason": "unknown_role"})
            continue
        quote = _text(proposal.get("evidence_quote"))
        justification = _text(proposal.get("justification"))
        if not quote or quote not in source_text:
            rejected.append({"index": str(index), "reason": "quote_not_in_stored_text"})
            continue
        role_claim = build_role_claim(
            source_name,
            role,
            source_text=source_text,
            evidence_quote=quote,
            justification=justification,
            policy_url=policy_url,
        )
        if role_claim is None:
            rejected.append({"index": str(index), "reason": "role_not_permitted_or_reason_missing"})
            continue
        reason = _validate_role_specific(
            proposal,
            role=role,
            text=source_text,
            quote=quote,
            source_name=source_name,
            evidence_context=evidence_context,
            analysis_classification=analysis_classification,
        )
        if reason:
            rejected.append({"index": str(index), "reason": reason})
            continue
        status = proposal.get("status")
        if status not in {"confirmed", "plausible_unverified"}:
            rejected.append({"index": str(index), "reason": "claim_status_not_qualified"})
            continue
        if role in {"buying_evidence", "buyer_route"} and status != "confirmed":
            rejected.append({"index": str(index), "reason": "explicit_claim_not_confirmed"})
            continue
        confidence = proposal.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            rejected.append({"index": str(index), "reason": "confidence_out_of_range"})
            continue
        if role in {"buying_evidence", "buyer_route"} and confidence < 0.8:
            rejected.append({"index": str(index), "reason": "explicit_claim_confidence_too_low"})
            continue
        if status == "confirmed" and confidence < 0.8:
            status = "plausible_unverified"
        if (
            role == "india_evidence"
            and status == "confirmed"
            and not _INDIA_OPERATIONS_RE.search(quote)
        ):
            status = "plausible_unverified"

        company = _text(proposal.get("company_name")) or None
        trigger_type = proposal.get("trigger_type")
        event_date_quote = _text(proposal.get("event_date_quote")) or None
        event_date = _event_date_iso(event_date_quote)
        if role == "company_trigger" and not event_date:
            status = "plausible_unverified"
        route = _text(proposal.get("route")) or None
        key = (
            role,
            quote,
            company or "",
            event_date_quote or "",
            route or "",
        )
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        claims.append({
            **role_claim,
            "source_item_id": source_item_id,
            "submission_id": submission_id,
            "source_url": source_url,
            "canonical_url": canonical_url,
            "publication_date": _date_iso(publication_date),
            "status": status,
            "confidence": float(confidence),
            "company_name": company,
            "company_identity_resolution": "unresolved" if company else None,
            "trigger_type": trigger_type,
            "event_date_quote": event_date_quote,
            "event_date": event_date,
            "route": route,
            "evidence_source": proposal.get("evidence_source"),
        })

    return claims, {
        "proposed": len(proposals),
        "accepted": len(claims),
        "rejected": rejected,
        "duplicate_claims": duplicates,
    }


def revalidate_stored_claims(
    observation: Any,
    submission: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Revalidate saved claims against current policy and the stored item."""
    import json

    try:
        metadata = json.loads(getattr(observation, "metadata_json", None) or "{}")
    except (TypeError, json.JSONDecodeError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    proposals = metadata.get("commercial_evidence_claims")
    if not isinstance(proposals, list):
        return [], {"proposed": 0, "accepted": 0, "rejected": [], "duplicate_claims": 0}
    try:
        analysis = json.loads(getattr(submission, "analysis_json", None) or "{}")
    except (TypeError, json.JSONDecodeError):
        analysis = {}
    if not isinstance(analysis, dict):
        analysis = {}
    return validate_evidence_claims(
        proposals,
        source_name=getattr(observation, "source_name", None) or "",
        source_text=getattr(submission, "text", None) or "",
        policy_url=metadata.get("feed"),
        source_item_id=(
            getattr(observation, "native_id", None)
            or getattr(submission, "id", 0)
        ),
        submission_id=getattr(submission, "id", 0),
        source_url=getattr(observation, "source_url", None) or "",
        canonical_url=getattr(observation, "canonical_url", None) or "",
        publication_date=getattr(observation, "source_created_at", None),
        evidence_context=metadata,
        analysis_classification=analysis,
    )


def build_company_trigger_watchlist(db: Session) -> list[dict[str, Any]]:
    """Return validated account triggers separately from ranked opportunities."""
    rows = db.execute(
        select(SourceObservation, Submission).join(
            Submission,
            Submission.id == SourceObservation.submission_id,
        ).order_by(SourceObservation.source_created_at.desc())
    ).all()
    validated_claims: list[dict[str, Any]] = []
    for observation, submission in rows:
        claims, _diagnostics = revalidate_stored_claims(observation, submission)
        for claim in claims:
            company = claim.get("company_name")
            if not company or claim.get("role") not in {
                "company_trigger",
                "india_evidence",
                "buying_evidence",
                "buyer_route",
            }:
                continue
            validated_claims.append(claim)

    linked_claims = link_company_evidence(validated_claims)
    by_company: dict[str, dict[str, Any]] = {}
    for claim in linked_claims:
        company = claim.get("company_name")
        company_key = claim.get("company_identity_key")
        if not company or not company_key:
            continue
        row = by_company.setdefault(company_key, {
            "company_name": company,
            "identity_resolution": claim.get("company_identity_resolution"),
            "identity_resolution_reason": claim.get(
                "company_identity_resolution_reason"
            ),
            "identity_source_item_count": claim.get(
                "company_identity_source_item_count", 1
            ),
            "company_triggers": [],
            "india_evidence": [],
            "buying_evidence": [],
            "buyer_routes": [],
        })
        category = {
            "company_trigger": "company_triggers",
            "india_evidence": "india_evidence",
            "buying_evidence": "buying_evidence",
            "buyer_route": "buyer_routes",
        }[claim["role"]]
        entry = {
            "submission_id": claim.get("submission_id"),
            "source_item_id": claim.get("source_item_id"),
            "source_url": claim.get("source_url"),
            "publication_date": claim.get("publication_date"),
            "event_date_quote": claim.get("event_date_quote"),
            "event_date": claim.get("event_date"),
            "role": claim.get("role"),
            "trigger_type": claim.get("trigger_type"),
            "evidence_quote": claim.get("evidence_quote"),
            "justification": claim.get("justification"),
            "status": claim.get("status"),
            "confidence": claim.get("confidence"),
            "route": claim.get("route"),
        }
        if not any(
            existing["source_url"] == entry["source_url"]
            and existing["evidence_quote"] == entry["evidence_quote"]
            for existing in row[category]
        ):
            row[category].append(entry)
    return sorted(by_company.values(), key=lambda row: row["company_name"].casefold())