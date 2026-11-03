import json
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import initialize_database
from app import source_policy
from app.commercial_evidence import validate_evidence_claims
from app.models import (
    ClusterEvidence,
    ProblemCluster,
    SourceObservation,
    Submission,
)
from app.services.discovery_report import (
    discovery_report_path,
    partition_discovery_items,
    write_discovery_opportunities_report,
)
from app.services.pipeline import run_pipeline
from app.pipeline_schemas import PipelineRunRequest


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    initialize_database(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _product_ready_analysis(quote: str) -> str:
    return json.dumps({
        "is_genuine_problem": True,
        "signal_type": "pain_point",
        "problem_statement": (
            "GA4 purchases do not match store orders; reconcile weekly."
        ),
        "affected_user": "eCommerce analytics lead",
        "context": "Measurement team compares GA4 to order exports weekly.",
        "category": "Software & IT",
        "industry": "eCommerce",
        "filter_reason_code": "KEEP_DEMAND",
        "section": "Analytics",
        "capability_readiness_1_to_5": 4,
        "section_client_fit_1_to_5": 5,
        "leverage_on_offerings_1_to_5": 5,
        "commercial_signal_1_to_5": 3,
        "effort_to_first_version_1_to_5": 4,
        "defensibility_1_to_5": 3,
        "smallest_first_version": (
            "BQ Agent / GA Tag Auditor check that emails a drift report"
        ),
        "first_client_type": "eCommerce analytics lead",
        "failure_risk_line": "Native GA4 tools may shrink demand",
        "software_buildability": "likely",
        "software_buildability_reason": "Narrow tabular compare of event streams.",
        "willingness_to_pay_signal": "suggested",
        "evidence_quotes": [quote],
        "urgency_1_to_5": 4,
        "frequency_signal_1_to_5": 4,
        "evidence_strength_1_to_5": 4,
        "tags": ["GA4", "BQ Agent"],
    })


def _add_product_ready_cluster(db, *, title: str = "GA4 purchase drift"):
    cluster = ProblemCluster(
        title=title,
        problem_statement=title,
        category="Software & IT",
        status="candidate",
    )
    db.add(cluster)
    db.flush()
    for index in range(3):
        quote = f"reconcile weekly by hand {index}"
        submission = Submission(
            text=(
                f"{title}. We reconcile weekly by hand {index}. "
                "Tag Auditor / BQ Agent style automation needed for GA4."
            ),
            source_name=[
                "Reddit",
                "Stack Exchange:stackoverflow",
                "Stack Exchange:datascience",
            ][index],
            source_url=f"https://example.test/{cluster.id}/{index}",
            submitted_by=f"author-{index}",
            analysis_status="completed",
            analysis_json=_product_ready_analysis(quote),
        )
        db.add(submission)
        db.flush()
        db.add(ClusterEvidence(
            cluster_id=cluster.id,
            submission_id=submission.id,
            is_independent=True,
            relationship_type="supports",
        ))
    db.commit()
    db.refresh(cluster)
    return cluster


def test_partition_keeps_only_product_ready_in_ranked():
    ranked, watchlist = partition_discovery_items([
        {
            "cluster_id": 1,
            "product_ready": True,
            "score": 70.0,
            "top10_eligible": True,
            "eligible_evidence_count": 3,
            "demand_metrics": {"keep_demand_count": 3},
        },
        {
            "cluster_id": 2,
            "product_ready": False,
            "score": None,
            "top10_eligible": False,
            "eligible_evidence_count": 2,
            "demand_metrics": {"keep_demand_count": 1},
        },
        {
            "cluster_id": 3,
            "product_ready": True,
            "score": 80.0,
            "top10_eligible": True,
            "eligible_evidence_count": 3,
            "demand_metrics": {"keep_demand_count": 3},
        },
    ])
    assert [item["cluster_id"] for item in ranked] == [3, 1]
    assert ranked[0]["rank"] == 1
    assert [item["cluster_id"] for item in watchlist] == [2]


def test_write_discovery_report_canonical_path_and_overwrite(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("REPORT_OUTPUT_DIR", str(tmp_path))
    get_settings.cache_clear()
    Session = _session_factory()
    from app.main import build_opportunity_summary

    with Session() as db:
        _add_product_ready_cluster(db, title="GA4 purchase drift A")
        first = write_discovery_opportunities_report(
            db,
            build_opportunity_summary,
            run_id="run-1",
            run_date=date(2026, 10, 29),
            collection_summary=__import__(
                "app.services.source_yield",
                fromlist=["normalize_source_yield"],
            ).normalize_source_yield(
                requested_sources=["hacker_news"],
                collection={
                    "hacker_news": {
                        "status": "completed",
                        "fetched": 1,
                        "created": 1,
                    },
                },
            ),
            stage_counts={
                "analysis": {"processed": 1, "completed": 1},
                "clustering": {"considered": 1, "created": 1},
            },
        )
        path = Path(first["path"])
        assert path == tmp_path / "opportunities_2026-10-29.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "GA4 purchase drift A" in text
        assert "Weekly Business Leads" in text
        assert "Validated problem opportunities" not in text
        assert "Why now:" in text
        assert "First customer acquisition strategy:" in text
        assert "Service vs standalone product:" in text
        assert "[ ] Accepted - Reason: ____" in text
        assert "[ ] Rejected - Reason: ____" in text
        assert "not validated demand" in text
        assert "Shortfall" in text
        assert "hacker_news" in text
        assert "not_requested" in text
        assert "Pipeline funnel" in text or "Source and evidence health" in text

        _add_product_ready_cluster(db, title="GA4 purchase drift B")
        second = write_discovery_opportunities_report(
            db,
            build_opportunity_summary,
            run_id="run-2",
            run_date=date(2026, 10, 29),
        )
        assert second["path"] == first["path"]
        assert len(list(tmp_path.glob("opportunities_*.md"))) == 1
        updated = path.read_text(encoding="utf-8")
        assert "GA4 purchase drift B" in updated
        assert "run-2" in updated
    get_settings.cache_clear()


def test_write_discovery_report_insufficient_evidence_explains_shortfall(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("REPORT_OUTPUT_DIR", str(tmp_path))
    get_settings.cache_clear()
    Session = _session_factory()
    from app.main import build_opportunity_summary

    with Session() as db:
        cluster = ProblemCluster(
            title="Thin signal only",
            problem_statement="Not enough evidence",
        )
        submission = Submission(
            text="A short GA4 note without enough independent evidence.",
            source_name="reddit",
            analysis_status="completed",
            analysis_json=json.dumps({
                "is_genuine_problem": True,
                "signal_type": "pain_point",
                "filter_reason_code": "KEEP_DEMAND",
                "section": "Analytics",
                "software_buildability": "likely",
                "evidence_quotes": ["GA4 note"],
                "urgency_1_to_5": 3,
                "frequency_signal_1_to_5": 3,
                "evidence_strength_1_to_5": 3,
            }),
        )
        db.add_all([cluster, submission])
        db.flush()
        db.add(ClusterEvidence(
            cluster_id=cluster.id,
            submission_id=submission.id,
        ))
        db.commit()

        result = write_discovery_opportunities_report(
            db,
            build_opportunity_summary,
            run_date=date(2026, 10, 29),
        )
        assert result["ranked_count"] == 0
        assert result["shortfall"] is True
        text = Path(result["path"]).read_text(encoding="utf-8")
        assert (
            "No weekly business leads" in text
            or "No company-level commercial leads" in text
        )
        assert "watchlist" in text.casefold()
    get_settings.cache_clear()


def test_pipeline_run_writes_discovery_report_without_email(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("REPORT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("CEO_EMAIL", raising=False)
    monkeypatch.delenv("GMAIL_CLIENT_SECRETS_FILE", raising=False)
    get_settings.cache_clear()


    Session = _session_factory()
    from app.main import build_opportunity_summary

    def collect(limit, db):
        submission = Submission(
            text=(
                "GA4 purchases do not match store orders; we reconcile "
                "weekly by hand across three teams."
            ),
            source_name="Hacker News",
            source_url="https://news.ycombinator.com/item?id=999001",
            analysis_status="pending",
        )
        db.add(submission)
        db.commit()
        db.refresh(submission)
        return {
            "source": "Hacker News",
            "fetched": 1,
            "created": 1,
            "duplicates_skipped": 0,
            "irrelevant_skipped": 0,
            "submission_ids": [submission.id],
        }

    monkeypatch.setattr("app.services.pipeline.collect_hacker_news", collect)

    def analyze(text, _source, _language, source_context=None):
        from app.schemas import ProblemExtraction

        payload = json.loads(_product_ready_analysis("reconcile weekly by hand"))
        payload["problem_statement"] = text[:120]
        payload["context"] = text[:160]
        return ProblemExtraction.model_validate(payload), "fake-model"

    # One item cannot be product_ready; report must still be written honestly.
    with Session() as db:
        result = run_pipeline(
            PipelineRunRequest(sources=["hacker_news"]),
            db,
            analyzer=analyze,
            summarize_opportunity=build_opportunity_summary,
        )
        assert result["discovery_report"]["status"] == "written"
        path = Path(result["discovery_report"]["path"])
        assert path.parent == tmp_path
        assert path.name.startswith("opportunities_")
        assert path.exists()
        body = path.read_text(encoding="utf-8")
        assert "Tatvic opportunities" in body
        assert "Watchlist" in body or "No weekly business leads" in body
        # Same-day second run overwrites a single file.
        again = run_pipeline(
            PipelineRunRequest(sources=["hacker_news"]),
            db,
            analyzer=analyze,
            summarize_opportunity=build_opportunity_summary,
        )
        assert again["discovery_report"]["path"] == result["discovery_report"]["path"]
        assert len(list(tmp_path.glob("opportunities_*.md"))) == 1

    assert discovery_report_path(
        run_date=date.fromisoformat(result["discovery_report"]["run_date"]),
        output_dir=tmp_path,
    ).exists()
    get_settings.cache_clear()


def test_report_includes_unclustered_trigger_without_ranking_it(
    tmp_path,
    monkeypatch,
):
    feed = "https://news.example/feed"
    policy_text = (
        "## 5. Articles and blogs\n"
        f"- {feed} (roles: company_trigger, india_evidence)\n"
    )
    monkeypatch.setattr(
        source_policy,
        "_read_sources_text",
        lambda: policy_text,
    )
    quote = "Acme Ltd expanded operations in Bengaluru on 15 October 2026."
    claims, _ = validate_evidence_claims(
        [{
            "role": "company_trigger",
            "evidence_quote": quote,
            "justification": "Article states a dated company expansion.",
            "confidence": 0.9,
            "status": "confirmed",
            "company_name": "Acme Ltd",
            "trigger_type": "expansion",
            "event_date_quote": "15 October 2026",
        }],
        source_name="articles",
        source_text=quote,
        policy_url=feed,
        source_item_id=77,
        submission_id=1,
        source_url="https://news.example/story/77",
        canonical_url="https://news.example/story/77",
        publication_date=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    Session = _session_factory()
    with Session() as db:
        submission = Submission(
            text=quote,
            source_name="articles",
            source_url="https://news.example/story/77",
            analysis_status="pending",
        )
        db.add(submission)
        db.flush()
        claims[0]["submission_id"] = submission.id
        db.add(SourceObservation(
            submission_id=submission.id,
            source_name="articles",
            source_url=submission.source_url,
            canonical_url=submission.source_url,
            native_id="77",
            metadata_json=json.dumps({
                "feed": feed,
                "commercial_evidence_claims": claims,
            }),
            source_created_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        ))
        db.commit()
        result = write_discovery_opportunities_report(
            db,
            lambda *_args: {},
            run_date=date(2026, 10, 29),
            output_dir=tmp_path,
        )
    body = Path(result["path"]).read_text(encoding="utf-8")
    assert result["ranked_count"] == 0
    assert "Company triggers to investigate (not ranked demand)" in body
    assert "Acme Ltd" in body
    assert "15 October 2026" in body
    assert quote in body
    assert "does not establish technical pain, buying intent, or buyer identity" in body
    source_policy.load_source_policy.cache_clear()


def test_deferred_clustering_count_rendered_in_discovery_report_markdown(
    tmp_path,
    monkeypatch,
):
    """
    When stage_counts["clustering"] includes "deferred_clustering_count",
    the discovery report markdown must include that key so operators can see
    how many eligible items were not yet clustered.

    Validates: Requirements 2.5
    """
    from app.services.discovery_report import render_discovery_opportunities_markdown

    monkeypatch.setenv("REPORT_OUTPUT_DIR", str(tmp_path))
    get_settings.cache_clear()

    stage_counts = {
        "analysis": {
            "processed": 5,
            "completed": 5,
            "deferred": 2,
        },
        "clustering": {
            "considered": 3,
            "matched": 1,
            "created": 2,
            "duplicates": 0,
            "already_clustered": 0,
            "needs_review": 0,
            "failed": 0,
            "skipped": 4,
            "deferred_clustering_count": 4,
        },
    }

    markdown = render_discovery_opportunities_markdown(
        run_date=date(2026, 11, 1),
        ranked=[],
        watchlist=[],
        run_id="test-run-deferred",
        stage_counts=stage_counts,
    )

    assert "deferred_clustering_count=4" in markdown, (
        "The discovery report markdown must render 'deferred_clustering_count' "
        "in the Pipeline funnel section when it is present in stage_counts"
    )
    assert "Pipeline funnel" in markdown
    get_settings.cache_clear()
