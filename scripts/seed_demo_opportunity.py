#!/usr/bin/env python3
"""Seed one product-ready GA4 opportunity you can show in the UI.

Uses the RUBRIC.md KEEP_DEMAND example (purchase mismatch / weekly reconcile)
with 3 independent authors on **demand-role** sources only (Stack Exchange +
Reddit). Hacker News is capability corroboration under SOURCES.md and must not
be used to satisfy the demand floor. Safe to re-run (skips if demo cluster
title already exists).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import ClusterEvidence, ProblemCluster, Submission  # noqa: E402
from app.services.gemini import PROMPT_VERSION  # noqa: E402

DEMO_TITLE = "GA4 purchases do not match store orders (weekly manual reconcile)"


def _analysis(quote: str, *, filter_reason_code: str = "KEEP_DEMAND") -> dict:
    return {
        "is_genuine_problem": True,
        "signal_type": "pain_point",
        "problem_statement": (
            "GA4 purchases do not match store orders, so practitioners "
            "reconcile weekly by hand."
        ),
        "affected_user": "eCommerce analytics lead",
        "context": (
            "Measurement team compares GA4 ecommerce purchases to the "
            "order system every week because totals disagree."
        ),
        "current_workaround": "Manual spreadsheet reconciliation every Friday",
        "solution_request": (
            "An automated check that flags GA4 vs order drift early"
        ),
        "category": "Software & IT",
        "industry": "eCommerce",
        "section": "Analytics",
        "vertical": "eCommerce",
        "item_kind": "demand" if filter_reason_code == "KEEP_DEMAND" else "capability",
        "filter_reason_code": filter_reason_code,
        "filter_reason_sentence": (
            "Concrete practitioner friction in GA4 measurement with a "
            "clear Tatvic delivery path via Tag Auditor / BQ Agent."
        ),
        "capability_readiness_1_to_5": 4,
        "section_client_fit_1_to_5": 5,
        "leverage_on_offerings_1_to_5": 5,
        "commercial_signal_1_to_5": 3,
        "effort_to_first_version_1_to_5": 4,
        "defensibility_1_to_5": 3,
        "component_evidence": [
            "Weekly hand reconcile of GA4 vs store orders",
            "Fits GA Tag Auditor and BQ Agent measurement programmes",
        ],
        "smallest_first_version": (
            "BQ Agent / GA Tag Auditor check that compares GA4 purchase "
            "events to order exports and emails a drift report"
        ),
        "first_client_type": "eCommerce analytics lead",
        "failure_risk_line": (
            "Native GA4 debug tools or a one-off BigQuery SQL job may "
            "shrink willingness to buy an agent"
        ),
        "software_buildability": "likely",
        "software_buildability_reason": (
            "Comparison of two tabular event streams is a narrow, "
            "automable first version."
        ),
        "willingness_to_pay_signal": "suggested",
        "evidence_quotes": [quote],
        "noise_reason": None,
        "rejection_reason": None,
        "urgency_1_to_5": 4,
        "frequency_signal_1_to_5": 4,
        "evidence_strength_1_to_5": 4,
        "tags": ["GA4", "ecommerce", "reconciliation", "BQ Agent"],
        "uncertainty_notes": [],
    }


# Three demand-role sources (SOURCES.md). Optional HN row is capability only.
DEMAND_ITEMS = [
    {
        "source_name": "Stack Exchange:stackoverflow",
        "source_url": (
            "https://stackoverflow.com/questions/9000001/"
            "ga4-purchases-dont-match-store-orders"
        ),
        "submitted_by": "analytics_lead_eu",
        "quote": "we reconcile weekly by hand",
        "text": (
            "GA4 purchases don't match store orders; we reconcile weekly "
            "by hand\n\n"
            "Our Shopify order totals and GA4 purchase events disagree "
            "every week. The analytics team spends Friday afternoon "
            "reconciling in Sheets. Looking for a durable check, not "
            "another one-off BigQuery query."
        ),
    },
    {
        "source_name": "Reddit",
        "source_url": (
            "https://www.reddit.com/r/GoogleAnalytics/comments/demo01/"
            "ga4_purchase_mismatch/"
        ),
        "submitted_by": "gtm_ops_apac",
        "quote": "GA4 purchases don't match store orders",
        "text": (
            "GA4 purchases don't match store orders for our DTC brand\n\n"
            "Same pain as others here — purchase counts in GA4 are off "
            "vs the warehouse. We still reconcile weekly by hand before "
            "the Monday leadership pack. Tag Auditor style automation "
            "would help."
        ),
    },
    {
        "source_name": "Stack Exchange:datascience",
        "source_url": (
            "https://datascience.stackexchange.com/questions/9000002/"
            "ga4-purchase-export-mismatch"
        ),
        "submitted_by": "measurement_consultant",
        "quote": "reconcile weekly by hand before the Monday leadership pack",
        "text": (
            "GA4 purchase export mismatch vs warehouse orders\n\n"
            "Client's GA4 purchases don't match store orders. Analysts "
            "reconcile weekly by hand before the Monday leadership pack. "
            "Is anyone shipping a small BQ Agent style monitor for this?"
        ),
    },
]

# Capability corroboration — must NOT satisfy the demand floor alone.
CAPABILITY_ITEM = {
    "source_name": "Hacker News",
    "source_url": "https://news.ycombinator.com/item?id=90000001",
    "submitted_by": "hn_capability_note",
    "quote": "GA4 Measurement Protocol docs mention purchase reconciliation",
    "text": (
        "Show HN: Notes on GA4 Measurement Protocol purchase reconciliation\n\n"
        "GA4 Measurement Protocol docs mention purchase reconciliation "
        "patterns for server-side event quality. Capability context only."
    ),
    "filter_reason_code": "KEEP_CAPABILITY",
}


def main() -> int:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = (
            db.query(ProblemCluster)
            .filter(ProblemCluster.title == DEMO_TITLE)
            .first()
        )
        if existing is not None:
            print(f"Demo cluster already exists id={existing.id}")
            print("Open http://127.0.0.1:5173/#/opportunities")
            return 0

        cluster = ProblemCluster(
            title=DEMO_TITLE,
            problem_statement=(
                "GA4 purchases do not match store orders, forcing weekly "
                "manual reconciliation."
            ),
            affected_user="eCommerce analytics lead",
            category="Software & IT",
            industry="eCommerce",
            status="candidate",
        )
        db.add(cluster)
        db.flush()

        now = datetime.now(timezone.utc)
        for item in DEMAND_ITEMS:
            quote = item["quote"]
            analysis = _analysis(quote)
            if quote not in item["text"]:
                raise SystemExit(f"Quote missing from text: {quote}")
            submission = Submission(
                text=item["text"],
                source_name=item["source_name"],
                source_url=item["source_url"],
                submitted_by=item["submitted_by"],
                language="en",
                created_at=now,
                analysis_status="completed",
                analysis_json=json.dumps(analysis),
                model_name="seed-demo-rubric-v0.1",
                prompt_version=PROMPT_VERSION,
                severity_score=4.0,
                frequency_score=4.0,
            )
            db.add(submission)
            db.flush()
            db.add(
                ClusterEvidence(
                    cluster_id=cluster.id,
                    submission_id=submission.id,
                    is_independent=True,
                    relationship_type="supports",
                )
            )

        cap = CAPABILITY_ITEM
        cap_analysis = _analysis(
            cap["quote"],
            filter_reason_code=cap["filter_reason_code"],
        )
        if cap["quote"] not in cap["text"]:
            raise SystemExit(f"Quote missing from text: {cap['quote']}")
        cap_submission = Submission(
            text=cap["text"],
            source_name=cap["source_name"],
            source_url=cap["source_url"],
            submitted_by=cap["submitted_by"],
            language="en",
            created_at=now,
            analysis_status="completed",
            analysis_json=json.dumps(cap_analysis),
            model_name="seed-demo-rubric-v0.1",
            prompt_version=PROMPT_VERSION,
            severity_score=3.0,
            frequency_score=2.0,
        )
        db.add(cap_submission)
        db.flush()
        db.add(
            ClusterEvidence(
                cluster_id=cluster.id,
                submission_id=cap_submission.id,
                is_independent=True,
                relationship_type="supports",
            )
        )

        db.commit()
        print(f"Seeded demo opportunity cluster_id={cluster.id}")
        print("Demand-role evidence: SE + Reddit + SE:datascience (3).")
        print("HN attached as KEEP_CAPABILITY corroboration only.")
        print("Refresh Discover / Opportunities — expect a scored GA4 row.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
