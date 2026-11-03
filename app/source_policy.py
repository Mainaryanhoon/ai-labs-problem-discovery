"""Runtime source policy derived from ``docs/tatvic/SOURCES.md``.

This module never invents feeds or hosts. URLs and scrape hosts are taken
from the user-maintained policy file (plus env overrides that still must
pass the policy host allowlist). Proposed expansion feeds stay documented
here as **blocked** until they appear in SOURCES.md.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SOURCES_PATH = Path(__file__).resolve().parent.parent / "docs" / "tatvic" / "SOURCES.md"

# Candidate expansions from Phase 7 catalogue. **Not collected** unless the
# exact URL also appears in SOURCES.md (and the host is allowlisted there).
PROPOSED_POLICY_ENTRIES: tuple[dict[str, str], ...] = (
    {
        "name": "Economic Times Tech RSS",
        "url": "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms",
        "hosts": "economictimes.indiatimes.com",
        "role": "company_trigger / india_evidence",
        "section": "Articles and blogs — add feed + scrape host",
    },
    {
        "name": "Economic Times Startups RSS",
        "url": "https://economictimes.indiatimes.com/tech/startups/rssfeeds/13357300.cms",
        "hosts": "economictimes.indiatimes.com",
        "role": "company_trigger / india_evidence",
        "section": "Articles and blogs — add feed + scrape host",
    },
    {
        "name": "YourStory RSS",
        "url": "https://yourstory.com/feed",
        "hosts": "yourstory.com",
        "role": "company_trigger / india_evidence",
        "section": "Articles and blogs — add feed + scrape host",
    },
    {
        "name": "Inc42 RSS",
        "url": "https://inc42.com/feed",
        "hosts": "inc42.com",
        "role": "company_trigger / india_evidence",
        "section": "Articles and blogs — add feed + scrape host",
    },
    {
        "name": "LiveMint Technology RSS",
        "url": "https://www.livemint.com/rss/technology",
        "hosts": "livemint.com",
        "role": "company_trigger / india_evidence",
        "section": "Articles and blogs — add feed + scrape host",
    },
    {
        "name": "LiveMint Companies RSS",
        "url": "https://www.livemint.com/rss/companies",
        "hosts": "livemint.com",
        "role": "company_trigger / india_evidence",
        "section": "Articles and blogs — add feed + scrape host",
    },
    {
        "name": "Google Marketing Platform blog RSS",
        "url": "https://www.blog.google/products/marketingplatform/rss/",
        "hosts": "blog.google",
        "role": "capability",
        "section": "Articles and blogs — add feed (host already allowlisted)",
    },
    {
        "name": "Google Analytics products blog RSS",
        "url": "https://blog.google/products/marketingplatform/analytics/rss/",
        "hosts": "blog.google",
        "role": "capability",
        "section": "Articles and blogs — add feed (host already allowlisted)",
    },
    {
        "name": "Looker release notes Atom",
        "url": "https://cloud.google.com/feeds/looker-release-notes.xml",
        "hosts": "cloud.google.com",
        "role": "capability",
        "section": "Official release notes — add feed URL",
    },
    {
        "name": "Vertex AI release notes Atom",
        "url": "https://cloud.google.com/feeds/vertex-ai-release-notes.xml",
        "hosts": "cloud.google.com",
        "role": "capability",
        "section": "Official release notes — add feed URL",
    },
    {
        "name": "Dataform release notes Atom",
        "url": "https://cloud.google.com/feeds/dataform-release-notes.xml",
        "hosts": "cloud.google.com",
        "role": "capability",
        "section": "Official release notes — add feed URL",
    },
    {
        "name": "Google Developer Forums Data Analytics RSS",
        "url": "https://discuss.google.dev/c/google-cloud/data-analytics/86.rss",
        "hosts": "discuss.google.dev",
        "role": "context (demand only after explicit policy change)",
        "section": "New subsection + scrape host discuss.google.dev",
    },
)

_HTTPS_URL = re.compile(r"https://[^\s<>\]\)\"']+")
_EVIDENCE_ROLE_NAMES = (
    "demand",
    "capability",
    "context",
    "company_trigger",
    "india_evidence",
    "buying_evidence",
    "buyer_route",
    "market",
    "idea",
)
_POLICY_SOURCE_LABELS: dict[str, tuple[str, ...]] = {
    "official_docs": ("official google documentation and release notes",),
    "official docs": ("official google documentation and release notes",),
    "stack_exchange": ("stack exchange",),
    "stack exchange": ("stack exchange",),
    "reddit": ("reddit",),
    "hacker_news": ("hacker news",),
    "hacker news": ("hacker news",),
    "articles": (
        "articles and blogs",
        "india company-news rss feeds",
        "google marketing/data product feeds",
    ),
    "article": (
        "articles and blogs",
        "india company-news rss feeds",
        "google marketing/data product feeds",
    ),
    "google_developer_forums": (
        "google developer forums",
        "google developer forums — data analytics",
    ),
    "google developer forums": (
        "google developer forums",
        "google developer forums — data analytics",
    ),
    "youtube": ("youtube",),
    "x": ("x",),
    "twitter": ("x",),
}


def _read_sources_text() -> str:
    try:
        return SOURCES_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


def _urls_in(text: str) -> list[str]:
    found: list[str] = []
    for match in _HTTPS_URL.findall(text or ""):
        url = match.rstrip(".,;)]}>\"'")
        if url not in found:
            found.append(url)
    return found


def _hosts_from_sources(text: str) -> tuple[str, ...]:
    hosts: list[str] = []
    for line in text.splitlines():
        if "Hosts that may be scraped" not in line and "hosts that may be scraped" not in line.casefold():
            # Also accept backtick-listed hosts on the scrape line.
            pass
        lowered = line.casefold()
        if "scraped" in lowered and ("host" in lowered or "`" in line):
            for raw in re.findall(r"`([^`]+)`", line):
                host = raw.strip().lower().removeprefix("www.")
                if host and "." in host and host not in hosts:
                    hosts.append(host)
    return tuple(hosts)


def _section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s+\d+\.\s+{re.escape(heading)}.*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    nxt = re.search(r"^##\s+\d+\.", text[start:], re.MULTILINE)
    end = start + nxt.start() if nxt else len(text)
    return text[start:end]


@lru_cache
def load_source_policy() -> dict[str, Any]:
    text = _read_sources_text()
    all_urls = _urls_in(text)
    article_section = _section(text, "Articles and blogs")
    docs_section = _section(text, "Official release notes")
    article_feeds = _urls_in(article_section)
    docs_feeds = [
        url for url in _urls_in(docs_section)
        if url.endswith(".xml") or "release-notes" in url
    ]
    forum_urls = [
        url for url in all_urls
        if "discuss.google.dev" in urlparse(url).netloc.lower()
    ]
    return {
        "article_feeds": tuple(article_feeds),
        "docs_feeds": tuple(docs_feeds),
        "scrape_hosts": _hosts_from_sources(text),
        "forum_feed_urls": tuple(forum_urls),
        "all_policy_urls": tuple(all_urls),
        "raw_loaded": bool(text.strip()),
    }


def host_suffixes() -> tuple[str, ...]:
    return load_source_policy()["scrape_hosts"]


def host_allowed(url: str, *, extra_hosts: tuple[str, ...] = ()) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower().removeprefix("www.")
    allowed = tuple(host_suffixes()) + extra_hosts
    return any(host == suffix or host.endswith("." + suffix) for suffix in allowed)


def policy_article_feeds() -> list[str]:
    return list(load_source_policy()["article_feeds"])


def policy_docs_feeds() -> list[str]:
    return list(load_source_policy()["docs_feeds"])


def policy_roles_for_url(url: str) -> frozenset[str] | None:
    """Return roles explicitly annotated beside an exact policy URL."""
    target = (url or "").strip().rstrip("/")
    if not target:
        return None
    text = _read_sources_text()
    for line in text.splitlines():
        if not any(item.rstrip("/") == target for item in _urls_in(line)):
            continue
        role_clause = re.search(
            r"\broles?\s*[:=]\s*([^|;]+)",
            line,
            re.IGNORECASE,
        )
        if role_clause is None:
            return None
        clause = role_clause.group(1).casefold()
        return frozenset(
            role for role in _EVIDENCE_ROLE_NAMES
            if re.search(rf"\b{re.escape(role)}\b", clause)
        )
    return None


def policy_roles_for_source(source_name: str | None) -> frozenset[str]:
    """Return canonical roles from the current source priority table."""
    key = (source_name or "").strip().casefold().replace("-", "_")
    if key.startswith("stack exchange"):
        key = "stack_exchange"
    if key.startswith("reddit"):
        key = "reddit"
    labels = _POLICY_SOURCE_LABELS.get(key)
    if labels is None:
        return frozenset()
    text = _read_sources_text()
    section = _section(text, "Pilot sources and roles")
    matched_roles: set[str] = set()
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        row_source = cells[0].casefold()
        if not any(row_source == label or row_source.startswith(label) for label in labels):
            continue
        role_text = cells[1].casefold()
        if "none" in role_text and "pilot" in role_text:
            continue
        if "corroboration" in role_text or key in {"hacker_news", "hacker news"}:
            matched_roles.update(["capability_corroboration", "context"])
        matched_roles.update(
            role
            for role in _EVIDENCE_ROLE_NAMES
            if re.search(rf"\b{re.escape(role)}\b", role_text)
        )
    return frozenset(matched_roles)


def policy_forum_feeds() -> list[str]:
    """Google Developer Forums RSS URLs listed in SOURCES.md, else empty."""
    return [
        url for url in load_source_policy()["forum_feed_urls"]
        if host_allowed(url, extra_hosts=("discuss.google.dev",))
        or "discuss.google.dev" in urlparse(url).netloc.lower()
    ]


def policy_contains_url(url: str) -> bool:
    target = (url or "").strip().rstrip("/")
    if not target:
        return False
    return any(
        item.rstrip("/") == target
        for item in load_source_policy()["all_policy_urls"]
    )


def missing_proposed_policy_entries() -> list[dict[str, str]]:
    """Proposed catalogue feeds that are not yet in SOURCES.md."""
    missing = []
    for entry in PROPOSED_POLICY_ENTRIES:
        if not policy_contains_url(entry["url"]):
            missing.append(entry)
    return missing


def forum_collection_configured() -> bool:
    return bool(policy_forum_feeds()) and any(
        "discuss.google.dev" in suffix or suffix == "discuss.google.dev"
        for suffix in host_suffixes()
    ) or bool(policy_forum_feeds())
