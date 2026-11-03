"""Commercial qualification fields (provenance-backed; no fabrication)."""

from __future__ import annotations

from typing import Any, Literal

from app.commercial_evidence import (
    canonical_company_name,
    link_company_evidence,
)

IndiaRelevance = Literal["confirmed", "plausible_unverified", "none"]


def infer_india_relevance(
    *,
    analyses: list[dict[str, Any]],
    submissions: list[Any],
    india_evidence_claims: list[dict[str, Any]] | None = None,
) -> tuple[IndiaRelevance, str | None]:
    """Require a stored, source-quoted india_evidence role claim."""
    text_by_id = {
        getattr(submission, "id", None): getattr(submission, "text", None)
        for submission in submissions
    }
    statuses = []
    evidence = []
    for claim in india_evidence_claims or []:
        quote = claim.get("evidence_quote")
        reason = claim.get("justification")
        status = claim.get("status", "confirmed")
        stored_text = text_by_id.get(claim.get("submission_id"))
        if (
            claim.get("role") != "india_evidence"
            or not isinstance(quote, str)
            or not quote.strip()
            or not isinstance(stored_text, str)
            or quote not in stored_text
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            continue
        if status in {"confirmed", "plausible_unverified"}:
            statuses.append(status)
            evidence.append(f'{reason.strip()} Evidence: "{quote}"')
    if "confirmed" in statuses:
        index = statuses.index("confirmed")
        return "confirmed", evidence[index]
    if statuses:
        return "plausible_unverified", evidence[0]
    return "none", None


def build_commercial_qualification(
    *,
    cluster_title: str | None,
    section: str | None,
    offering_angle: str | None,
    smallest_first_version: str | None,
    first_client_type: str | None,
    failure_risk_line: str | None,
    demand_analyses: list[dict[str, Any]],
    demand_submissions: list[Any],
    india_evidence_claims: list[dict[str, Any]] | None = None,
    commercial_claims: list[dict[str, Any]] | None = None,
    commercial_claim_diagnostics: dict[str, Any] | None = None,
    evidence_submissions: list[Any] | None = None,
    supporting_capability_count: int,
    product_ready: bool,
) -> dict[str, Any]:
    """Build commercial qualification without inventing company-level facts.

    With the current approved source set, named companies and verified outreach
    routes are typically unavailable. Those fields stay empty and
    ``company_level_available`` is false.
    """
    all_claims = link_company_evidence(commercial_claims or [])
    triggers = [claim for claim in all_claims if claim.get("role") == "company_trigger"]
    india_claims = [claim for claim in all_claims if claim.get("role") == "india_evidence"]
    buying_claims = [claim for claim in all_claims if claim.get("role") == "buying_evidence"]
    buyer_route_claims = [claim for claim in all_claims if claim.get("role") == "buyer_route"]
    company_name = next(
        (claim.get("company_name") for claim in triggers if claim.get("company_name")),
        None,
    )
    company_key = canonical_company_name(company_name)
    matched_india_claims = [
        claim
        for claim in (india_evidence_claims or india_claims)
        if not company_key
        or canonical_company_name(claim.get("company_name")) == company_key
    ]
    india, india_evidence = infer_india_relevance(
        analyses=demand_analyses,
        submissions=evidence_submissions or demand_submissions,
        india_evidence_claims=matched_india_claims,
    )
    evidence_references = [
        {
            "submission_id": claim.get("submission_id"),
            "source_item_id": claim.get("source_item_id"),
            "source_url": claim.get("source_url"),
            "canonical_url": claim.get("canonical_url"),
            "publication_date": claim.get("publication_date"),
            "event_date_quote": claim.get("event_date_quote"),
            "role": claim.get("role"),
            "evidence_quote": claim.get("evidence_quote"),
            "justification": claim.get("justification"),
            "status": claim.get("status"),
            "company_name": claim.get("company_name"),
        }
        for claim in all_claims
    ]
    for submission in demand_submissions:
        url = getattr(submission, "source_url", None)
        sid = getattr(submission, "id", None)
        if (url or sid is not None) and not any(
            reference.get("submission_id") == sid
            and reference.get("role") == "demand"
            for reference in evidence_references
        ):
            evidence_references.append({
                "submission_id": sid,
                "source_name": getattr(submission, "source_name", None),
                "source_url": url,
                "role": "demand",
                "category": "demand",
            })

    evidence_submissions = evidence_submissions or demand_submissions
    text_by_id = {
        getattr(submission, "id", None): getattr(submission, "text", None)
        for submission in evidence_submissions
    }
    trigger_companies = {
        claim.get("company_identity_key")
        for claim in triggers
        if claim.get("company_identity_key")
    }
    india_companies = {
        claim.get("company_identity_key")
        for claim in india_claims
        if claim.get("company_identity_key") and claim.get("status") == "confirmed"
    }
    buying_companies = {
        claim.get("company_identity_key")
        for claim in buying_claims
        if claim.get("company_identity_key")
    }
    resolved_companies = {
        claim.get("company_identity_key")
        for claim in all_claims
        if claim.get("company_identity_key")
        and claim.get("company_identity_resolution") == "canonical_exact_name"
    }
    demand_items_by_company: dict[str, list[tuple[str | None, str | None]]] = {}
    demand_authors_by_company: dict[str, set[str]] = {}
    submissions_by_id = {
        getattr(submission, "id", None): submission
        for submission in evidence_submissions or demand_submissions
    }
    for claim in all_claims:
        company_key_for_claim = claim.get("company_identity_key")
        submission_id = claim.get("submission_id")
        submission = submissions_by_id.get(submission_id)
        if (
            claim.get("role") != "demand"
            or claim.get("status") != "confirmed"
            or not company_key_for_claim
            or not isinstance(submission_id, int)
            or submission is None
        ):
            continue
        canonical_url = claim.get("canonical_url") or claim.get("source_url")
        quote_key = canonical_company_name(claim.get("evidence_quote"))
        canonical_key = (
            str(canonical_url).casefold()
            if isinstance(canonical_url, str) and canonical_url.strip()
            else None
        )
        seen_items = demand_items_by_company.setdefault(
            company_key_for_claim,
            [],
        )
        if any(
            (canonical_key and canonical_key == known_url)
            or (quote_key and quote_key == known_quote)
            for known_url, known_quote in seen_items
        ):
            continue
        seen_items.append((canonical_key, quote_key))
        author = getattr(submission, "submitted_by", None)
        if isinstance(author, str) and author.strip():
            demand_authors_by_company.setdefault(
                company_key_for_claim,
                set(),
            ).add(author.strip().casefold())
    company_specific_demand = {
        company
        for company, item_ids in demand_items_by_company.items()
        if len(item_ids) >= 3
        and len(demand_authors_by_company.get(company, set())) >= 2
    }
    # A report row requires company-wide linkage across source items; a single
    # article's multiple roles are not independent identity corroboration.
    resolved_companies &= {
        claim.get("company_identity_key")
        for claim in all_claims
        if claim.get("company_identity_source_item_count", 0) >= 2
    }
    qualifying_companies = (
        trigger_companies
        & india_companies
        & buying_companies
        & resolved_companies
        & company_specific_demand
    )
    is_commercial_lead = bool(qualifying_companies)
    if qualifying_companies:
        lead_key = next(iter(sorted(qualifying_companies)))
        company_name = next(
            (
                c.get("company_name")
                for c in all_claims
                if c.get("company_identity_key") == lead_key
                and c.get("company_name")
            ),
            company_name,
        )
        company_key = lead_key

    matched_triggers = [
        t for t in triggers
        if not company_key or t.get("company_identity_key") == company_key
    ]
    matched_buying = [
        b for b in buying_claims
        if not company_key or b.get("company_identity_key") == company_key
    ]
    matched_routes = [
        r for r in buyer_route_claims
        if not company_key or r.get("company_identity_key") == company_key
    ]

    has_trigger = bool(company_key and company_key in trigger_companies)
    has_india = bool(company_key and company_key in india_companies)
    has_buying = bool(company_key and company_key in buying_companies)
    has_identity = bool(company_key and company_key in resolved_companies)
    has_demand = bool(company_key and company_key in company_specific_demand)
    has_route = bool(
        company_key
        and any(r.get("company_identity_key") == company_key for r in buyer_route_claims)
    )

    unknowns = []
    if not triggers:
        unknowns.append("No evidence-backed company trigger is available.")
    if not india_claims:
        unknowns.append("No authorized India evidence claim is available.")
    if not buying_claims:
        unknowns.append("No explicit buying or procurement evidence is available.")
    if not buyer_route_claims:
        unknowns.append("No verified public buyer route is available.")
    if triggers and not is_commercial_lead:
        unknowns.append(
            "A company trigger does not by itself establish India operations, "
            "company-specific technical pain, or buying intent."
        )
    if commercial_claim_diagnostics and commercial_claim_diagnostics.get("rejected"):
        unknowns.append(
            f"{len(commercial_claim_diagnostics['rejected'])} proposed evidence claim(s) "
            "were rejected by source-policy or evidence validation."
        )

    verified_routes = [
        {
            "company_name": claim.get("company_name"),
            "company_identity_resolution": claim.get("company_identity_resolution"),
            "company_identity_resolution_reason": claim.get(
                "company_identity_resolution_reason"
            ),
            "route": claim.get("route"),
            "source_url": claim.get("source_url"),
            "evidence_quote": claim.get("evidence_quote"),
            "justification": claim.get("justification"),
            "status": claim.get("status"),
        }
        for claim in matched_routes
    ]
    trigger_rows = [
        {
            "company_name": claim.get("company_name"),
            "identity_resolution": claim.get("company_identity_resolution", "unresolved"),
            "identity_resolution_reason": claim.get(
                "company_identity_resolution_reason"
            ),
            "identity_source_item_count": claim.get(
                "company_identity_source_item_count", 1
            ),
            "trigger_type": claim.get("trigger_type"),
            "event_date": claim.get("event_date"),
            "event_date_quote": claim.get("event_date_quote"),
            "publication_date": claim.get("publication_date"),
            "source_url": claim.get("source_url"),
            "evidence_quote": claim.get("evidence_quote"),
            "justification": claim.get("justification"),
            "status": claim.get("status"),
            "confidence": claim.get("confidence"),
        }
        for claim in matched_triggers
    ]
    buying_rows = [
        {
            "company_name": claim.get("company_name"),
            "source_url": claim.get("source_url"),
            "publication_date": claim.get("publication_date"),
            "evidence_quote": claim.get("evidence_quote"),
            "justification": claim.get("justification"),
            "status": claim.get("status"),
        }
        for claim in matched_buying
    ]
    if india == "none":
        unknowns.append("India relevance is none because no valid India claim was found.")
    if supporting_capability_count:
        unknowns.append(
            f"{supporting_capability_count} capability/context item(s) available "
            "as corroboration only — not demand-floor evidence."
        )

    qualification_gates = {
        "company_trigger": {
            "passed": has_trigger,
            "status": "PASSED" if has_trigger else "FAILED",
            "evidence_count": len(trigger_rows),
            "citations": [
                {
                    "source_url": t.get("source_url"),
                    "publication_date": t.get("publication_date"),
                    "event_date": t.get("event_date"),
                    "event_date_quote": t.get("event_date_quote"),
                    "evidence_quote": t.get("evidence_quote"),
                    "trigger_type": t.get("trigger_type"),
                    "status": t.get("status"),
                }
                for t in trigger_rows
            ],
            "reason": (
                f"{len(trigger_rows)} dated company trigger(s) verified"
                if has_trigger
                else (
                    f"No verified company trigger for {company_name}"
                    if company_name
                    else "No company trigger identified"
                )
            ),
        },
        "india_presence": {
            "passed": has_india,
            "status": "PASSED" if has_india else "FAILED",
            "evidence_count": len(matched_india_claims),
            "citations": [
                {
                    "source_url": c.get("source_url"),
                    "publication_date": c.get("publication_date"),
                    "evidence_quote": c.get("evidence_quote"),
                    "status": c.get("status"),
                }
                for c in matched_india_claims
            ],
            "reason": (
                f"India presence confirmed ({india_evidence})"
                if has_india
                else (
                    "Plausible but unverified India presence"
                    if india == "plausible_unverified"
                    else (
                        f"No verified India presence found for {company_name}"
                        if company_name
                        else "No India evidence available"
                    )
                )
            ),
            "uncertainty": (
                "Plausible unverified: location mentioned without verified India operations"
                if india == "plausible_unverified"
                else None
            ),
        },
        "buying_evidence": {
            "passed": has_buying,
            "status": "PASSED" if has_buying else "FAILED",
            "evidence_count": len(buying_rows),
            "citations": [
                {
                    "source_url": b.get("source_url"),
                    "publication_date": b.get("publication_date"),
                    "evidence_quote": b.get("evidence_quote"),
                    "status": b.get("status"),
                }
                for b in buying_rows
            ],
            "reason": (
                f"{len(buying_rows)} explicit procurement/buying evidence item(s)"
                if has_buying
                else (
                    f"No explicit procurement or provider search found for {company_name}"
                    if company_name
                    else "No procurement or buying evidence found"
                )
            ),
        },
        "company_identity_resolution": {
            "passed": has_identity,
            "status": "PASSED" if has_identity else "FAILED",
            "resolution": (
                "canonical_exact_name"
                if has_identity
                else (
                    next(
                        (
                            c.get("company_identity_resolution")
                            for c in all_claims
                            if c.get("company_identity_key") == company_key
                        ),
                        "unresolved",
                    )
                    if company_name
                    else "unavailable"
                )
            ),
            "reason": (
                "Exact canonical name verified across multiple source items"
                if has_identity
                else (
                    next(
                        (
                            c.get("company_identity_resolution_reason")
                            for c in all_claims
                            if c.get("company_identity_key") == company_key
                        ),
                        "Single source item or ambiguous identity; independent corroboration required.",
                    )
                    if company_name
                    else "No company name identified"
                )
            ),
        },
        "company_specific_demand": {
            "passed": has_demand,
            "status": "PASSED" if has_demand else "FAILED",
            "items_count": len(demand_items_by_company.get(company_key or "", [])),
            "authors_count": len(demand_authors_by_company.get(company_key or "", set())),
            "reason": (
                f"≥3 independent demand items from ≥2 authors verified for {company_name}"
                if has_demand
                else (
                    f"Insufficient company-specific demand for {company_name} (found {len(demand_items_by_company.get(company_key or '', []))} items from {len(demand_authors_by_company.get(company_key or '', set()))} authors; requires ≥3 items, ≥2 authors)"
                    if company_name
                    else "No company-specific demand items"
                )
            ),
        },
        "verified_buyer_route": {
            "passed": has_route,
            "status": "PASSED" if has_route else "FAILED",
            "routes": verified_routes,
            "reason": (
                f"{len(verified_routes)} verified public purposeful contact route(s)"
                if has_route
                else (
                    f"No verified public purposeful contact route for {company_name}"
                    if company_name
                    else "No outreach route found"
                )
            ),
        },
    }

    company_level_available = bool(company_name)

    return {
        "india_relevance": india,
        "company_name": company_name,
        "company_identity_resolution": (
            "canonical_exact_name"
            if company_key in resolved_companies
            else "unresolved"
            if company_name
            else None
        ),
        "company_website": None,
        "company_india_evidence": india_evidence,
        "problem_summary": cluster_title,
        "business_trigger": trigger_rows[0] if trigger_rows else None,
        "company_triggers": trigger_rows,
        "india_evidence_claims": [
            claim for claim in india_claims
        ],
        "buying_evidence": buying_rows or None,
        "buyer_routes": verified_routes,
        "buyer_function": first_client_type,
        "buyer_name": None,
        "verified_outreach_route": verified_routes[0] if verified_routes else None,
        "tatvic_offering_angle": offering_angle,
        "smallest_first_version": smallest_first_version,
        "first_client_type": first_client_type,
        "unknowns_to_validate": unknowns,
        "failure_risk": failure_risk_line,
        "evidence_references": evidence_references,
        "commercial_claim_diagnostics": commercial_claim_diagnostics or {
            "proposed": 0,
            "accepted": 0,
            "rejected": [],
            "duplicate_claims": 0,
        },
        "commercial_score_version": "v0.2-commercial",
        "section": section,
        "company_level_available": company_level_available,
        "is_commercial_lead": is_commercial_lead,
        "is_validated_problem_opportunity": bool(product_ready)
        and not is_commercial_lead,
        "qualification_gates": qualification_gates,
        "qualification_note": (
            "Company-level commercial lead unavailable: approved sources "
            "did not identify a named company, dated trigger, or verified "
            "outreach route. Problem may still be product-ready under v0.1."
        ),
    }
