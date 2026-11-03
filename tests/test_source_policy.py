from app import source_policy
from app.source_roles import permitted_item_roles, source_role


def test_missing_sources_policy_fails_closed(monkeypatch):
    monkeypatch.setattr(source_policy, "_read_sources_text", lambda: "")
    source_policy.load_source_policy.cache_clear()
    try:
        policy = source_policy.load_source_policy()
        assert policy["article_feeds"] == ()
        assert policy["docs_feeds"] == ()
        assert policy["scrape_hosts"] == ()
        assert source_policy.policy_article_feeds() == []
        assert source_policy.policy_docs_feeds() == []
        assert not source_policy.host_allowed(
            "https://cloud.google.com/blog/example"
        )
    finally:
        source_policy.load_source_policy.cache_clear()


def test_approved_feed_is_enabled_by_exact_policy_entry():
    assert source_policy.policy_contains_url(
        "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms"
    )


def test_current_pilot_roles_and_hosts_are_policy_derived():
    assert source_role("Stack Exchange:stackoverflow") == "demand"
    assert source_role("Reddit") == "demand"
    assert source_role("Hacker News") == "capability_corroboration"
    assert source_role("X") == "idea"
    assert source_policy.policy_roles_for_url(
        "https://yourstory.com/feed"
    ) == {"company_trigger", "india_evidence"}
    assert source_policy.policy_roles_for_url(
        "https://discuss.google.dev/c/google-cloud/data-analytics/86.rss"
    ) == {"context"}
    assert source_policy.host_allowed("https://yourstory.com/feed")
    assert source_policy.host_allowed(
        "https://discuss.google.dev/c/google-cloud/data-analytics/86.rss"
    )
    assert not source_policy.host_allowed("https://unapproved.example/feed")


def test_article_policy_accepts_feed_paths_only_inside_approved_section(monkeypatch):
    text = """## 5. Articles and blogs

- https://yourstory.com/feed
- https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms
- https://www.livemint.com/rss/technology

Hosts that may be scraped: `yourstory.com` `economictimes.indiatimes.com` `livemint.com`

## 6. Hacker News
- https://unapproved.example/rss
"""
    monkeypatch.setattr(source_policy, "_read_sources_text", lambda: text)
    source_policy.load_source_policy.cache_clear()
    try:
        assert source_policy.policy_article_feeds() == [
            "https://yourstory.com/feed",
            "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms",
            "https://www.livemint.com/rss/technology",
        ]
        assert "https://unapproved.example/rss" not in (
            source_policy.policy_article_feeds()
        )
    finally:
        source_policy.load_source_policy.cache_clear()


def test_roles_require_explicit_permission_on_the_exact_policy_url(monkeypatch):
    feed = "https://news.example/feed"
    text = f"## 5. Articles and blogs\n- {feed} (roles: context, company_trigger)\n"
    monkeypatch.setattr(source_policy, "_read_sources_text", lambda: text)
    assert permitted_item_roles("articles", feed) == {
        "context",
        "company_trigger",
    }
    assert permitted_item_roles("articles", "https://news.example/other") == {
        "context"
    }
    assert permitted_item_roles("unknown-source", feed) == set()