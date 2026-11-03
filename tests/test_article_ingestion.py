from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  registers tables
from app import article_ingestion
from app.models import Submission
from app.db import Base
from app.docs_filter_helpers import fetch_not_before, hygiene_drop_reason, should_fetch_new_item

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)
FEED = "https://cloud.google.com/blog/products/data-analytics/rss"
PAGE = "https://cloud.google.com/blog/products/data-analytics/ga4-cost"


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def _rss(url: str, published: str, title: str, summary: str) -> str:
    return f"""<?xml version="1.0"?>
    <rss version="2.0"><channel><item>
    <guid isPermaLink="false">{url}</guid>
      <title>{title}</title>
      <link>{url}</link>
      <pubDate>{published}</pubDate>
      <description>{summary}</description>
    </item></channel></rss>"""


SUMMARY = (
    "GA4 export to BigQuery doubled the query cost for the analytics team "
    "after scheduled queries were added"
)


def test_cold_start_keeps_recent_items_and_skips_old_ones(monkeypatch):
    db = _session()
    monkeypatch.setattr(article_ingestion, "article_feeds", lambda: [FEED])
    calls: list[str] = []

    def resolve_page(url: str):
        calls.append(url)
        return ("BigQuery slot cost rose after the GA4 export schedule changed " * 3, "firecrawl")

    xml = f"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item>
        <title>GA4 BigQuery cost</title>
        <link>{PAGE}</link>
        <pubDate>Mon, 20 Oct 2026 00:00:00 GMT</pubDate>
        <description>{SUMMARY}</description>
      </item>
      <item>
        <title>Old note</title>
        <link>https://cloud.google.com/blog/products/data-analytics/old</link>
        <pubDate>Thu, 01 Jan 2026 00:00:00 GMT</pubDate>
        <description>{SUMMARY}</description>
      </item>
    </channel></rss>"""

    result = article_ingestion.collect_articles(
        8,
        db,
        fetch_feed=lambda _url: xml,
        resolve_page=resolve_page,
        now=NOW,
    )
    assert result["created"] == 1
    assert result["outside_budget"] == 1
    assert calls == [PAGE]
    assert result["submission_metadata"][str(result["submission_ids"][0])]["fetch_method"] == "firecrawl"


def test_known_url_is_not_fetched_again(monkeypatch):
    db = _session()
    monkeypatch.setattr(article_ingestion, "article_feeds", lambda: [FEED])
    xml = _rss(PAGE, "Mon, 20 Oct 2026 00:00:00 GMT", "GA4 BigQuery cost", SUMMARY)
    calls: list[str] = []

    def resolve_page(url: str):
        calls.append(url)
        return (SUMMARY + " and analysts reconcile it by hand each week", "firecrawl")
    first = article_ingestion.collect_articles(
        8, db, fetch_feed=lambda _url: xml, resolve_page=resolve_page, now=NOW,
    )
    second = article_ingestion.collect_articles(
        8, db, fetch_feed=lambda _url: xml, resolve_page=resolve_page, now=NOW,
    )
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["duplicates_skipped"] >= 1
    assert second["pages_fetched"] == 0
    assert calls == [PAGE]


def test_forum_known_url_is_not_inserted_again(monkeypatch):
    db = _session()
    feed = "https://discuss.google.dev/c/google-cloud/data-analytics/86.rss"
    page = "https://discuss.google.dev/t/tdx-attestation-on-a3-high-pzid-verification-fails/409800"
    monkeypatch.setattr(article_ingestion, "article_feeds", lambda: [feed])
    xml = _rss(page, "Mon, 20 Oct 2026 00:00:00 GMT", "TDX attestation", SUMMARY)
    first = article_ingestion.collect_articles(
        8,
        db,
        fetch_feed=lambda _url: xml,
        resolve_page=lambda _url: (SUMMARY + " and operators retry attestation weekly", "rss"),
        now=NOW,
    )
    second = article_ingestion.collect_articles(
        8,
        db,
        fetch_feed=lambda _url: xml,
        resolve_page=lambda _url: (SUMMARY, "rss"),
        now=NOW,
        ignore_cursor=True,
    )
    assert first["created"] == 1
    assert db.get(Submission, first["submission_ids"][0]).source_name == (
        "Google Developer Forums"
    )
    assert second["created"] == 0
    assert second["duplicates_skipped"] >= 1


def test_later_run_uses_cursor_not_a_fresh_60_day_window(monkeypatch):
    db = _session()
    monkeypatch.setattr(article_ingestion, "article_feeds", lambda: [FEED])
    recent = _rss(PAGE, "Thu, 16 Oct 2026 00:00:00 GMT", "GA4 BigQuery cost", SUMMARY)
    article_ingestion.collect_articles(
        8,
        db,
        fetch_feed=lambda _url: recent,
        resolve_page=lambda _url: (SUMMARY, "firecrawl"),
        now=NOW,
    )
    older = "https://cloud.google.com/blog/products/data-analytics/older-gap"
    calls: list[str] = []
    gap_xml = _rss(older, "Thu, 01 Oct 2026 00:00:00 GMT", "Older gap", SUMMARY)
    result = article_ingestion.collect_articles(
        8,
        db,
        fetch_feed=lambda _url: gap_xml,
        resolve_page=lambda url: calls.append(url) or (SUMMARY, "firecrawl"),
        now=NOW,
    )
    assert result["created"] == 0
    assert result["outside_budget"] == 1
    assert calls == []


def test_deprecation_bypasses_the_fetch_cursor(monkeypatch):
    db = _session()
    monkeypatch.setattr(article_ingestion, "article_feeds", lambda: [FEED])
    xml = _rss(
        PAGE,
        "Mon, 01 Jan 2024 00:00:00 GMT",
        "Universal Analytics deprecation deadline",
        "Forced migration off Universal Analytics still needs a tag audit plan for clients",
    )
    result = article_ingestion.collect_articles(
        8,
        db,
        fetch_feed=lambda _url: xml,
        resolve_page=lambda _url: (None, "none"),
        now=NOW,
    )
    assert result["created"] == 1
    assert result["submission_metadata"][str(result["submission_ids"][0])]["fetch_method"] == "rss"


def test_playwright_fallback_when_firecrawl_is_empty(monkeypatch):
    monkeypatch.setattr(article_ingestion, "_firecrawl_scrape", lambda _url: None)
    monkeypatch.setattr(
        article_ingestion,
        "_playwright_scrape",
        lambda _url: " ".join(["tracking"] * 20),
    )
    text, method = article_ingestion.resolve_page_text(PAGE)
    assert method == "playwright"
    assert text is not None and "tracking" in text


def test_playwright_rejects_host_outside_source_policy():
    assert article_ingestion._playwright_scrape(
        "https://unapproved.example/article"
    ) is None


def test_firecrawl_is_preferred_over_playwright(monkeypatch):
    monkeypatch.setattr(
        article_ingestion,
        "_firecrawl_scrape",
        lambda _url: " ".join(["bigquery"] * 20),
    )
    called = {"playwright": False}

    def playwright(_url: str):
        called["playwright"] = True
        return " ".join(["unused"] * 20)

    monkeypatch.setattr(article_ingestion, "_playwright_scrape", playwright)
    _text, method = article_ingestion.resolve_page_text(PAGE)
    assert method == "firecrawl"
    assert called["playwright"] is False


def test_firecrawl_errors_do_not_return_the_key(monkeypatch):
    class Settings:
        firecrawl_api_key = "super-secret-token"
        firecrawl_base_url = "https://api.firecrawl.dev"

    monkeypatch.setattr(article_ingestion, "get_settings", lambda: Settings())

    def boom(*_args, **_kwargs):
        raise article_ingestion.httpx.HTTPError("upstream failed")

    monkeypatch.setattr(article_ingestion.httpx, "post", boom)
    assert article_ingestion._firecrawl_scrape(PAGE) is None


def test_non_allowlisted_feed_urls_are_dropped(monkeypatch):
    class Settings:
        article_feed_urls = (
            "https://evil.example/rss,"
            "http://yourstory.com/feed,"
            "https://yourstory.com/feed"
        )
        firecrawl_api_key = ""
        firecrawl_base_url = "https://api.firecrawl.dev"

    monkeypatch.setattr(article_ingestion, "get_settings", lambda: Settings())
    assert article_ingestion.article_feeds() == [
        "https://yourstory.com/feed",
        *article_ingestion.HOMEPAGE_FALLBACK_FEEDS,
    ]


def test_dead_rss_urls_are_included_for_homepage_fallback():
    feeds = article_ingestion.article_feeds()
    assert "https://entrackr.com/feed" in feeds
    assert "https://www.financialexpress.com/feed/" in feeds
    assert article_ingestion.MAX_ARTICLE_FEEDS == 20
    assert len(feeds) <= article_ingestion.MAX_ARTICLE_FEEDS


def test_allowlisted_host_is_not_enough_without_exact_policy_feed(monkeypatch):
    class Settings:
        article_feed_urls = "https://blog.google/products/marketingplatform/rss/"

    monkeypatch.setattr(article_ingestion, "get_settings", lambda: Settings())
    assert article_ingestion.article_feeds() == list(
        article_ingestion.HOMEPAGE_FALLBACK_FEEDS
    )


def test_feed_redirect_to_unapproved_host_is_rejected(monkeypatch):
    off_host = article_ingestion.httpx.Response(
        200,
        text="<rss></rss>",
        request=article_ingestion.httpx.Request(
            "GET",
            "https://unapproved.example/feed",
        ),
    )
    monkeypatch.setattr(
        article_ingestion.httpx,
        "get",
        Mock(return_value=off_host),
    )
    with pytest.raises(ValueError, match="allowlisted"):
        article_ingestion._default_fetch_feed(FEED)
    article_ingestion.httpx.get.assert_called_once_with(
        FEED,
        timeout=30,
        follow_redirects=True,
    )


def test_blocked_feed_status_is_reported_without_summary_substitution(monkeypatch):
    db = _session()
    monkeypatch.setattr(article_ingestion, "article_feeds", lambda: [FEED])
    request = article_ingestion.httpx.Request("GET", FEED)
    response = article_ingestion.httpx.Response(403, request=request)
    error = article_ingestion.httpx.HTTPStatusError(
        "blocked",
        request=request,
        response=response,
    )
    result = article_ingestion.collect_articles(
        5,
        db,
        fetch_feed=lambda _url: (_ for _ in ()).throw(error),
        resolve_page=lambda _url: (None, "none", "unavailable"),
    )
    assert result["feed_statuses"] == [{
        "url": FEED,
        "status": "blocked",
        "http_status": 403,
    }]
    assert result["created"] == 0
    db.close()


def test_dead_rss_falls_back_to_homepage_scrape(monkeypatch):
    db = _session()
    feed = "https://entrackr.com/feed"
    monkeypatch.setattr(article_ingestion, "article_feeds", lambda: [feed])
    request = article_ingestion.httpx.Request("GET", feed)
    response = article_ingestion.httpx.Response(404, request=request)
    error = article_ingestion.httpx.HTTPStatusError(
        "missing",
        request=request,
        response=response,
    )
    homepage = "India startup funding and founder problems " * 40
    result = article_ingestion.collect_articles(
        5,
        db,
        fetch_feed=lambda _url: (_ for _ in ()).throw(error),
        resolve_page=lambda url: (homepage if url == "https://entrackr.com" else None, "playwright", "full_page"),
        ignore_cursor=True,
    )
    assert result["feed_failures"] == 0
    assert result["feed_statuses"][0]["status"] == "homepage_fallback"
    assert result["created"] == 1
    saved = db.query(Submission).one()
    assert saved.source_url == "https://entrackr.com"
    assert "Homepage Scrape" in saved.text
    db.close()


def test_fetch_budget_helpers():
    assert fetch_not_before(None, now=NOW) == NOW - timedelta(days=60)
    newest = NOW - timedelta(days=10)
    assert fetch_not_before(newest, now=NOW) == newest - timedelta(days=3)
    assert should_fetch_new_item(NOW - timedelta(days=90), now=NOW) is False
    assert should_fetch_new_item(
        NOW - timedelta(days=90),
        is_capability_deprecation=True,
        now=NOW,
    )
    assert hygiene_drop_reason(
        text="one two three four five six seven eight nine ten eleven twelve",
        created_at=NOW - timedelta(days=90),
        now=NOW,
    ) == "DROP_HYGIENE"
    assert hygiene_drop_reason(
        text="too short",
        created_at=NOW,
        now=NOW,
    ) == "DROP_HYGIENE"


def test_rss_parser_preserves_native_id_date_and_canonical_link():
    xml = _rss(
        PAGE,
        "Mon, 20 Oct 2026 00:00:00 GMT",
        "GA4 BigQuery release",
        SUMMARY,
    )
    item = article_ingestion.parse_feed_items(xml)[0]
    assert item["native_id"] == PAGE
    assert item["url"] == PAGE
    assert item["published"] == datetime(2026, 10, 20, tzinfo=timezone.utc)


def test_approved_hosts_are_allowlisted_and_unapproved_blocked():
    approved = [
        "https://cloud.google.com/blog/example",
        "https://docs.cloud.google.com/example",
        "https://blog.google/products/marketingplatform/example",
        "https://www.blog.google/products/marketingplatform/example",
        "https://yourstory.com/example",
        "https://economictimes.indiatimes.com/tech/example",
        "https://inc42.com/example",
        "https://www.livemint.com/technology/example",
        "https://livemint.com/companies/example",
        "https://discuss.google.dev/c/google-cloud/data-analytics/86",
    ]
    for url in approved:
        assert article_ingestion.host_allowed(url) is True, f"Host should be allowed: {url}"

    unapproved = [
        "https://unapproved.example/post",
        "https://x.com/user/status/123",
        "https://twitter.com/user/status/123",
        "http://cloud.google.com/insecure",  # non-https
    ]
    for url in unapproved:
        assert article_ingestion.host_allowed(url) is False, f"Host should be blocked: {url}"


def test_playwright_fallback_when_firecrawl_returns_insufficient_text(monkeypatch):
    monkeypatch.setattr(
        article_ingestion,
        "_firecrawl_scrape",
        lambda _url: "too short",
    )
    playwright_content = " ".join(["bigquery", "dataform", "pipeline", "cost"] * 5)
    monkeypatch.setattr(
        article_ingestion,
        "_playwright_scrape_with_status",
        lambda _url: (playwright_content, "full_page"),
    )
    text, method, status = article_ingestion.resolve_page_text_with_status(PAGE)
    assert method == "playwright"
    assert status == "full_page"
    assert "bigquery" in text


def test_playwright_blocks_redirect_to_unapproved_host(monkeypatch):
    monkeypatch.setattr(
        article_ingestion,
        "_playwright_scrape_with_status",
        lambda _url: (None, "redirect_blocked"),
    )
    text, method, status = article_ingestion.resolve_page_text_with_status(PAGE)
    assert text is None
    assert method == "none"
    assert status == "redirect_blocked"

