"""Acceptance tests for the India-first leads lane. Fixtures only, no network."""
import json
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.leads import models  # noqa: F401
from app.leads import extract, service
from app.leads.report import render_report
from app.leads.scoring import WEIGHT_VERSION, WEIGHTS, compute_score, india_cap_from_text
from app.models import Submission

TEXT_RETAIL = (
    "Bengaluru-based D2C founder says rising return-to-origin losses are eating margins across Indian "
    "retail brands. 'We lose nearly 18 percent of every cash-on-delivery order in tier-2 cities,' "
    "the founder said, adding that brands in India now budget crore-level amounts for fraud prevention "
    "after the recent RBI and GST changes. Three other Indian brands confirmed similar losses this quarter."
)
Q1 = "We lose nearly 18 percent of every cash-on-delivery order in tier-2 cities"
Q2 = "brands in India now budget crore-level amounts for fraud prevention"


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _sub(db, text, url="https://yourstory.com/a1", source="articles"):
    s = Submission(text=text, source_name=source, source_url=url, submitted_by="article-feed",
                   created_at=datetime.now(timezone.utc))
    db.add(s); db.commit()
    return s


def fake_llm(title="COD fraud and RTO prediction for Indian D2C brands", scores=None, delivery="product", quotes=None):
    payload = {
        "is_lead": True, "title": title,
        "problem": "D2C brands lose margin to RTO and COD fraud.",
        "who_pays": "Head of Growth / Founder at Indian D2C brands",
        "company": None,
        "why_now": "Recent RBI/GST changes and festive-season COD volume.",
        "first_customer_route": "Warm intros via D2C founder communities and Shopify India agencies.",
        "delivery_type": delivery,
        "what_to_build_v1": "COD-risk score API that flags high-RTO pin codes before ship.",
        "tatvic_fit_note": "Analytics + ML scoring on order data.",
        "evidence_quotes": quotes or [Q1, Q2],
        "india_quote": "brands in India now budget crore-level amounts for fraud prevention",
        "scores": scores or {"commercial_demand": 5, "problem_significance": 4, "buyer_path": 4,
                             "why_now": 4, "tatvic_fit": 1, "india_relevance": 5},
    }
    return lambda system, user: json.dumps(payload)


def test_non_tatvic_indian_founder_idea_becomes_a_lead():
    db = _session(); _sub(db, TEXT_RETAIL)
    f = service.run_extraction(db, fake_llm())
    assert f["saved_new"] == 1
    text, shown, _, _ = render_report(db, f, today=date(2026, 11, 2))
    assert len(shown) == 1 and "COD fraud" in text   # tatvic_fit=1 did not block it


def test_non_practitioner_author_not_dropped():
    db = _session(); s = _sub(db, TEXT_RETAIL); s.submitted_by = "CMO, Acme Retail"; db.commit()
    assert service.run_extraction(db, fake_llm())["saved_new"] == 1


def test_weights_sum_commercial_largest_india_present():
    assert sum(WEIGHTS.values()) == 100
    assert WEIGHTS["commercial_demand"] == max(WEIGHTS.values())
    assert WEIGHTS["india_relevance"] > 0
    assert WEIGHT_VERSION.startswith("v0.3")


def test_india_claim_is_capped_by_text():
    assert india_cap_from_text("Global chip maker posts quarterly earnings") == 1


def test_report_has_no_validated_and_starts_with_three_lines():
    db = _session(); _sub(db, TEXT_RETAIL); service.run_extraction(db, fake_llm())
    text, shown, _, _ = render_report(db, None, today=date(2026, 11, 2))
    assert "validated" not in text.casefold().replace("not validated demand", "")
    assert len(text.split("\n\n")[0].splitlines()) == 3
    assert len(shown) <= 5
    assert "## Top 5 leads" in text
    assert "What to Build (v1)" in text
    assert "Source Citations" in text
    assert "https://yourstory.com/a1" in text


def test_empty_week_message():
    db = _session()
    text, shown, _, _ = render_report(db, None, today=date(2026, 11, 2))
    assert shown == [] and text.splitlines()[0] == "0 new leads this week."


def test_every_lead_has_required_fields():
    db = _session(); _sub(db, TEXT_RETAIL); service.run_extraction(db, fake_llm())
    lead = db.query(models.Lead).one()
    assert lead.why_now and lead.first_customer_route and lead.delivery_type in {"service", "product"}
    assert lead.smallest_first_version
    assert lead.smallest_first_version.casefold() != lead.problem.casefold()


def test_v1_that_repeats_problem_is_rejected():
    payload = json.loads(fake_llm()("", ""))
    payload["what_to_build_v1"] = payload["problem"]
    assert extract.validate_lead(payload, TEXT_RETAIL) == (None, "v1_repeats_problem")


def test_what_to_build_v1_maps_to_smallest_first_version():
    payload = json.loads(fake_llm()("", ""))
    clean, reason = extract.validate_lead(payload, TEXT_RETAIL)
    assert reason == "ok" and clean is not None
    assert "what_to_build_v1" not in clean
    assert clean["smallest_first_version"] == payload["what_to_build_v1"]


def test_rejected_themes_are_injected_into_user_prompt():
    db = _session()
    _sub(db, TEXT_RETAIL)
    service.run_extraction(db, fake_llm())
    lead = db.query(models.Lead).one()
    service.record_feedback(db, lead.id, "rejected", "buyer too small")
    seen: dict[str, str] = {}

    def capture(system, user):
        seen["user"] = user
        return json.dumps({"is_lead": False, "reason": "matches_rejected_theme"})

    _sub(db, TEXT_RETAIL + " Another article.", url="https://yourstory.com/a2")
    service.run_extraction(db, capture)
    assert "<previously_rejected_themes>" in seen["user"]
    assert "buyer too small" in seen["user"]
    assert "</previously_rejected_themes>" in seen["user"]


def test_missing_why_now_or_bad_delivery_rejected():
    db = _session(); _sub(db, TEXT_RETAIL)
    bad = json.loads(fake_llm(delivery="saas")("", ""))
    assert extract.validate_lead(bad, TEXT_RETAIL)[0] is None
    bad2 = json.loads(fake_llm()("", "")); bad2["why_now"] = " "
    assert extract.validate_lead(bad2, TEXT_RETAIL)[0] is None


def test_fabricated_quote_is_rejected():
    db = _session(); _sub(db, TEXT_RETAIL)
    f = service.run_extraction(db, fake_llm(quotes=["This sentence never appeared in the article at all"]))
    assert f["saved_new"] == 0 and f["fail_reasons"].get("quote_not_verbatim") == 1


def test_idea_log_suppresses_repeat_next_week():
    db = _session(); _sub(db, TEXT_RETAIL); service.run_extraction(db, fake_llm())
    from app.leads.report import write_report
    import tempfile
    d = tempfile.mkdtemp()
    write_report(db, None, out_dir=d, today=date(2026, 11, 2))
    text, shown, _, _ = render_report(db, None, today=date(2026, 11, 9))
    assert shown == [] and text.splitlines()[0] == "0 new leads this week."


def test_same_idea_from_second_article_merges_not_duplicates():
    db = _session(); _sub(db, TEXT_RETAIL, url="https://yourstory.com/a1")
    _sub(db, TEXT_RETAIL + " Update.", url="https://inc42.com/a2")
    f = service.run_extraction(db, fake_llm())
    assert f["saved_new"] == 1 and f["merged_into_existing"] == 1
    assert db.query(models.Lead).count() == 1


def test_feedback_requires_reason_and_persists():
    db = _session(); _sub(db, TEXT_RETAIL); service.run_extraction(db, fake_llm())
    lead = db.query(models.Lead).one()
    with pytest.raises(ValueError):
        service.record_feedback(db, lead.id, "rejected", "   ")
    service.record_feedback(db, lead.id, "rejected", "buyer too small")
    assert db.query(models.LeadFeedback).one().reason == "buyer too small"
    assert db.get(models.Lead, lead.id).status == "rejected"
    assert service.top_rejection_reasons(db)[0][0] == "buyer too small"
    text, shown, repeats, watch = render_report(db, None, today=date(2026, 11, 2))
    assert shown == []
    assert all(item.id != lead.id for item in repeats + watch)
    assert f"Lead #{lead.id}" not in text


def test_feedback_api_records_lead_decision():
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    db = _session()
    _sub(db, TEXT_RETAIL)
    service.run_extraction(db, fake_llm())
    lead = db.query(models.Lead).one()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        response = client.post(
            f"/api/clusters/{lead.id}/feedback",
            json={"decision": "accepted", "reason": "Strong commercial signal"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 201
    body = response.json()
    assert body["lead_id"] == lead.id
    assert body["decision"] == "accepted"
    db.expire_all()
    assert db.get(models.Lead, lead.id).status == "accepted"


def test_rejected_leads_are_hidden_from_leads_api_and_email():
    from app.leads.report import generate_weekly_email_body
    from app.main import list_leads

    db = _session()
    _sub(db, TEXT_RETAIL)
    service.run_extraction(db, fake_llm())
    lead = db.query(models.Lead).one()
    service.record_feedback(db, lead.id, "rejected", "out of scope")

    assert db.get(models.Lead, lead.id).status == "rejected"
    body = list_leads(limit=100, offset=0, db=db)
    assert body["total"] == 0
    assert body["items"] == []
    plain, html_body, _ = generate_weekly_email_body(db, None, top_n=5)
    assert lead.title not in plain
    assert lead.title not in html_body


def test_generate_weekly_email_body_includes_summary_and_lead_fields():
    from app.leads.report import generate_weekly_email_body, render_report

    db = _session()
    _sub(db, TEXT_RETAIL)
    service.run_extraction(db, fake_llm())
    _, shown, repeats, watch = render_report(db, None, today=date(2026, 11, 2))
    plain, html_body, summary = generate_weekly_email_body(
        db,
        {"eligible": 1, "saved_new": 1, "collected": 3},
        top_n=5,
        shown=shown,
        repeats=repeats,
        watch=watch,
    )
    assert summary[0].startswith("1 new lead")
    assert "Top lead is" in summary[0]
    assert "COD fraud" in plain
    assert "Why now" in plain
    assert "First customer reach strategy" in plain
    assert "What to Build (v1)" in plain
    assert "COD-risk score API" in plain
    assert "Source Citations" in plain
    assert "https://yourstory.com/a1" in plain
    assert "Standalone Product" in plain or "Service" in plain
    assert Q1 in plain
    assert Q1 in html_body
    assert "What to Build (v1)" in html_body
    assert "Source Citations" in html_body
    assert '<a href="https://yourstory.com/a1">https://yourstory.com/a1</a>' in html_body
    assert "Top 5" in html_body
    assert "Internal Tatvic briefing" in plain


def test_x_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_X", raising=False)
    import app.services.pipeline as p
    src = open(p.__file__).read()
    assert 'os.getenv("ENABLE_X"' in src


def test_weekly_run_aggregates_optional_x_and_youtube(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path

    weekly_path = Path("scripts/weekly_run.py")
    spec = importlib.util.spec_from_file_location("weekly_run_under_test", weekly_path)
    weekly = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(weekly)

    db = _session()
    monkeypatch.setenv("ENABLE_X", "true")
    monkeypatch.setenv("ENABLE_YOUTUBE", "yes")
    monkeypatch.setenv("ENABLE_REDDIT", "true")
    monkeypatch.setenv("LEADS_COLLECT_LIMIT", "7")
    monkeypatch.setenv("LEADS_MAX_LLM_CALLS", "0")
    monkeypatch.setattr(weekly, "initialize_database", lambda: None)
    monkeypatch.setattr(weekly, "SessionLocal", lambda: db)

    def fake_articles(limit, db, ignore_cursor=False):
        return {
            "created": 3,
            "feed_statuses": [{"url": "https://yourstory.com/feed", "status": "fetched"}],
        }

    monkeypatch.setattr(
        "app.article_ingestion.collect_articles",
        fake_articles,
    )
    monkeypatch.setattr(
        weekly,
        "collect_x",
        lambda limit, db: (_ for _ in ()).throw(RuntimeError("apify down")),
    )
    monkeypatch.setattr(
        weekly,
        "collect_youtube",
        lambda limit, db: {"source": "youtube", "created": 4, "fetch_status": "fetched"},
    )
    monkeypatch.setattr(
        weekly,
        "collect_hacker_news",
        lambda limit, db: {"source": "hacker_news", "created": 2, "fetch_status": "fetched"},
    )
    monkeypatch.setattr(
        weekly,
        "collect_stack_exchange",
        lambda limit, site, db: {
            "source": f"stack_exchange:{site}",
            "created": 1,
            "fetch_status": "fetched",
        },
    )
    monkeypatch.setattr(
        weekly,
        "collect_reddit",
        lambda limit, db: (_ for _ in ()).throw(RuntimeError("reddit down")),
    )
    monkeypatch.setattr(
        weekly,
        "run_extraction",
        lambda db, llm, max_calls=40, sources=("articles",): {
            "eligible": 0,
            "sources": sources,
            "max_calls": max_calls,
        },
    )
    monkeypatch.setattr(weekly, "complete_json", lambda *a, **k: "{}")
    monkeypatch.setattr(weekly, "write_report", lambda db, funnel, **_: (str(tmp_path / "leads.md"), [], [], []))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "reports").mkdir(parents=True)

    assert weekly.main() == 0
    funnel = json.loads((tmp_path / "data" / "reports" / f"funnel_{date.today().isoformat()}.json").read_text())
    assert funnel["collected"] == 10  # 3 articles + 2 HN + 1 SE + 0 reddit + 0 X + 4 youtube
    x_status = next(item for item in funnel["feed_statuses"] if item.get("source") == "x")
    yt_status = next(item for item in funnel["feed_statuses"] if item.get("source") == "youtube")
    reddit_status = next(item for item in funnel["feed_statuses"] if item.get("source") == "reddit")
    hn_status = next(item for item in funnel["feed_statuses"] if item.get("source") == "hacker_news")
    assert x_status["status"] == "unavailable"
    assert x_status["error"] == "RuntimeError"
    assert reddit_status["status"] == "unavailable"
    assert hn_status["created"] == 2
    assert yt_status["created"] == 4


def test_first_feed_cannot_starve_other_feeds(monkeypatch):
    from app import article_ingestion as ai

    f1, f2 = "https://economictimes.indiatimes.com/tech/rssfeeds/1.cms", "https://yourstory.com/feed"
    monkeypatch.setattr(ai, "article_feeds", lambda: [f1, f2])
    monkeypatch.setattr(ai, "host_allowed", lambda url: True)

    def feed(url):
        host = url.split("/")[2]
        items = "".join(
            f"<item><title>T{i} {host}</title><link>https://{host}/p{i}</link><guid>{host}{i}</guid>"
            f"<description>{'India business problem ' * 40}</description></item>" for i in range(6))
        return f"<rss><channel>{items}</channel></rss>"

    db = _session()
    out = ai.collect_articles(
        6, db, fetch_feed=feed,
        resolve_page=lambda u: ("India business problem text " * 60, "playwright", "full_page"),
    )
    hosts = {s.source_url.split("/")[2] for s in db.query(Submission).all()}
    assert hosts == {"economictimes.indiatimes.com", "yourstory.com"}, out


def test_ignore_cursor_collects_items_older_than_stored_cursor(monkeypatch):
    from datetime import timedelta
    from app import article_ingestion as ai
    from app.provenance import record_source_provenance

    feed_url = "https://yourstory.com/feed"
    monkeypatch.setattr(ai, "article_feeds", lambda: [feed_url])
    monkeypatch.setattr(ai, "host_allowed", lambda url: True)
    db = _session()
    newest = Submission(text="x " * 100, source_name="articles", source_url="https://x.com/n", submitted_by="a")
    db.add(newest); db.flush()
    record_source_provenance(db, newest, native_id="n1", community="articles", source_url="https://x.com/n",
                             canonical_url="https://x.com/n", source_created_at=datetime.now(timezone.utc), metadata={})
    db.commit()
    old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    rss = (f"<rss><channel><item><title>Old</title><link>https://yourstory.com/old</link><guid>old1</guid>"
           f"<pubDate>{old}</pubDate><description>{'India ' * 120}</description></item></channel></rss>")
    page = lambda u: ("India business text " * 60, "playwright", "full_page")
    assert ai.collect_articles(5, db, fetch_feed=lambda u: rss, resolve_page=page)["created"] == 0
    assert ai.collect_articles(5, db, fetch_feed=lambda u: rss, resolve_page=page, ignore_cursor=True)["created"] == 1
