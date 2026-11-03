"""Article collector.

Discovery uses allowlisted RSS or Atom feeds. Full page text is fetched only
for URLs that are new and inside the fetch budget. Firecrawl runs first.
Playwright is the fallback. Feed text is untrusted data and is only stored.
"""

from __future__ import annotations

import hashlib
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.clustering import canonicalize_url
from app.config import get_settings
from app.db import get_db
from app.docs_filter_helpers import fails_min_word_gate, looks_like_deprecation, should_fetch_new_item
from app.ingestion import clean_text, parse_source_datetime
from app.models import Submission
from app.provenance import (
    known_native_ids,
    known_observation_urls,
    newest_source_created_at,
    record_source_provenance,
)
from app.source_policy import (
    host_allowed as policy_host_allowed,
    policy_article_feeds,
    policy_forum_feeds,
)

router = APIRouter(prefix="/api/ingestion", tags=["Ingestion"])

MAX_ARTICLE_FEEDS = 20
HOMEPAGE_FALLBACK_FEEDS = (
    "https://entrackr.com/feed",
    "https://www.financialexpress.com/feed/",
)

FeedFetcher = Callable[[str], str]
PageFetcher = Callable[[str], tuple[str | None, str]]
DetailedPageFetcher = Callable[[str], tuple[str | None, str, str]]


def host_allowed(url: str) -> bool:
    extra_hosts = tuple(
        (urlparse(feed).hostname or "").lower().removeprefix("www.")
        for feed in HOMEPAGE_FALLBACK_FEEDS
    )
    extra_hosts = tuple(host for host in extra_hosts if host)
    return policy_host_allowed(url, extra_hosts=extra_hosts)


def article_feeds() -> list[str]:
    raw = get_settings().article_feed_urls.strip()
    configured = list(dict.fromkeys([
        *policy_article_feeds(),
        *policy_forum_feeds(),
        *HOMEPAGE_FALLBACK_FEEDS,
    ]))
    candidates = raw.split(",") if raw else configured
    feeds: list[str] = []
    for part in candidates:
        url = part.strip()
        if (
            url
            and host_allowed(url)
            and url in configured
            and url not in feeds
        ):
            feeds.append(url)
    for url in HOMEPAGE_FALLBACK_FEEDS:
        if url not in feeds and host_allowed(url):
            feeds.append(url)
    return feeds


def parse_feed_datetime(value: str | None) -> datetime | None:
    parsed = parse_source_datetime(value)
    if parsed is not None:
        return parsed
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(node: ET.Element, name: str) -> str:
    for child in list(node):
        if _local_name(child.tag) == name and child.text:
            return child.text.strip()
    return ""


def _entry_link(node: ET.Element) -> str:
    link = _child_text(node, "link")
    if link.startswith("https://"):
        return link
    for child in list(node):
        if _local_name(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        rel = child.attrib.get("rel") or "alternate"
        if href.startswith("https://") and rel != "enclosure":
            return href
    return ""


def parse_feed_items(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, Any]] = []
    for node in root.iter():
        if _local_name(node.tag) not in {"item", "entry"}:
            continue
        title = clean_text(_child_text(node, "title"))
        summary = clean_text(
            _child_text(node, "description")
            or _child_text(node, "summary")
            or _child_text(node, "content")
        )
        published_raw = (
            _child_text(node, "pubDate")
            or _child_text(node, "published")
            or _child_text(node, "updated")
            or _child_text(node, "date")
        )
        items.append({
            "title": title,
            "url": _entry_link(node),
            "native_id": (
                _child_text(node, "guid")
                or _child_text(node, "id")
                or None
            ),
            "summary": summary,
            "published": parse_feed_datetime(published_raw),
        })
    return items


def _firecrawl_scrape(url: str) -> str | None:
    settings = get_settings()
    token = settings.firecrawl_api_key.strip()
    if not token or not host_allowed(url):
        return None
    base = settings.firecrawl_base_url.strip().rstrip("/") or "https://api.firecrawl.dev"
    try:
        response = httpx.post(
            f"{base}/v1/scrape",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": True,
            },
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    markdown = ""
    if isinstance(data, dict) and isinstance(data.get("markdown"), str):
        markdown = data["markdown"]
    elif isinstance(payload.get("markdown"), str):
        markdown = payload["markdown"]
    text = clean_text(markdown)
    return text or None


def _playwright_scrape(url: str) -> str | None:
    text, _status = _playwright_scrape_with_status(url)
    return text


def _playwright_scrape_with_status(url: str) -> tuple[str | None, str]:
    if not host_allowed(url):
        return None, "blocked"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "unavailable"
    text = ""
    response_status = None
    redirected_off_host = False
    try:
        with sync_playwright() as playwright:
            executable_path = next(
                (
                    path
                    for name in ("chromium", "chromium-browser", "google-chrome")
                    if (path := shutil.which(name))
                ),
                None,
            )
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=executable_path,
            )
            try:
                page = browser.new_page()
                def guard_route(route):
                    nonlocal redirected_off_host
                    if host_allowed(route.request.url):
                        route.continue_()
                    else:
                        redirected_off_host = True
                        route.abort()

                page.route("**/*", guard_route)
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
                response_status = response.status if response else None
                if not host_allowed(page.url):
                    return None, "redirect_blocked"
                text = ""
                for selector in ("article", "main", "body"):
                    locator = page.locator(selector)
                    if locator.count() == 0:
                        continue
                    text = locator.first.inner_text(timeout=5000)
                    if not fails_min_word_gate(text):
                        break
            finally:
                browser.close()
    except Exception:
        return None, "redirect_blocked" if redirected_off_host else "unavailable"
    if response_status in {401, 403, 429}:
        return None, "blocked"
    if response_status is not None and 300 <= response_status < 400:
        return None, "redirect_blocked"
    cleaned = clean_text(text)
    if not cleaned or fails_min_word_gate(cleaned):
        return None, "empty_or_insufficient"
    return cleaned, "full_page"


def resolve_page_text(url: str) -> tuple[str | None, str]:
    firecrawl_text = _firecrawl_scrape(url)
    if firecrawl_text and not fails_min_word_gate(firecrawl_text):
        return firecrawl_text, "firecrawl"
    playwright_text = _playwright_scrape(url)
    if playwright_text and not fails_min_word_gate(playwright_text):
        return playwright_text, "playwright"
    return None, "none"


def resolve_page_text_with_status(url: str) -> tuple[str | None, str, str]:
    firecrawl_text = _firecrawl_scrape(url)
    if firecrawl_text and not fails_min_word_gate(firecrawl_text):
        return firecrawl_text, "firecrawl", "full_page"
    settings = get_settings()
    firecrawl_status = (
        "not_configured"
        if not settings.firecrawl_api_key.strip()
        else "unavailable"
    )
    playwright_text, playwright_status = _playwright_scrape_with_status(url)
    if playwright_text:
        return playwright_text, "playwright", "full_page"
    return None, "none", playwright_status or firecrawl_status


def _default_fetch_feed(url: str) -> str:
    if not host_allowed(url):
        raise ValueError("Feed host is not allowlisted.")
    response = httpx.get(url, timeout=30, follow_redirects=True)
    final = str(response.url)
    if not host_allowed(final):
        raise ValueError("Feed redirect left the allowlisted hosts.")
    response.raise_for_status()
    return response.text


def collect_articles(
    limit: int = 8,
    db: Session | None = None,
    *,
    fetch_feed: FeedFetcher | None = None,
    resolve_page: PageFetcher | DetailedPageFetcher | None = None,
    now: datetime | None = None,
    ignore_cursor: bool = False,
) -> dict[str, Any]:
    if db is None:
        raise HTTPException(status_code=500, detail="Database session required.")

    fetch_feed = fetch_feed or _default_fetch_feed
    resolve_page = resolve_page or resolve_page_text_with_status
    known_ids: set[str] = set()
    known_urls: set[str] = set()
    for source_name in ("articles", "Google Developer Forums"):
        known_ids.update(known_native_ids(db, source_name))
        known_urls.update(known_observation_urls(db, source_name))
    # Weekly runs and newly added feeds must not be starved by a global cursor;
    # known_ids/known_urls already prevent re-ingesting stored items.
    newest = None if ignore_cursor else newest_source_created_at(db, "articles")
    feeds = article_feeds()[:MAX_ARTICLE_FEEDS]
    if not feeds:
        return {
            "source": "articles",
            "attempted": False,
            "fetch_status": "not_configured",
            "created": 0,
            "submission_ids": [],
            "submission_metadata": {},
        }
    feeds_requested = 0
    feeds_fetched = 0
    feed_failures = 0
    feed_statuses: list[dict[str, Any]] = []
    created = 0
    duplicates = 0
    outside_budget = 0
    skipped = 0
    pages_fetched = 0
    page_fetch_failures = 0
    submission_ids: list[int] = []
    metadata_by_id: dict[str, dict[str, Any]] = {}

    per_feed_cap = max(1, -(-limit // max(1, len(feeds))))  # ceil: no feed can starve the others
    for feed_url in feeds:
        feeds_requested += 1
        created_this_feed = 0
        entries: list[dict[str, Any]] | None = None
        try:
            feed_text = fetch_feed(feed_url)
        except httpx.HTTPStatusError as exc:
            skipped += 1
            feed_failures += 1
            status_code = exc.response.status_code
            feed_statuses.append({
                "url": feed_url,
                "status": (
                    "blocked"
                    if status_code in {401, 403, 429}
                    else "redirect_blocked"
                    if 300 <= status_code < 400
                    else "unavailable"
                ),
                "http_status": status_code,
            })
            parsed = urlparse(feed_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            page_result = resolve_page(base_url)
            text = page_result[0] if page_result else None
            if isinstance(text, str) and text.strip():
                entries = [{
                    "title": "Homepage Scrape",
                    "url": base_url,
                    "summary": text,
                }]
                feed_failures -= 1
            else:
                continue
        except (httpx.HTTPError, ValueError):
            skipped += 1
            feed_failures += 1
            feed_statuses.append({"url": feed_url, "status": "unavailable"})
            parsed = urlparse(feed_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            page_result = resolve_page(base_url)
            text = page_result[0] if page_result else None
            if isinstance(text, str) and text.strip():
                entries = [{
                    "title": "Homepage Scrape",
                    "url": base_url,
                    "summary": text,
                }]
                feed_failures -= 1
            else:
                continue
        if entries is None:
            try:
                entries = parse_feed_items(feed_text)
            except ET.ParseError:
                skipped += 1
                feed_failures += 1
                feed_statuses.append({"url": feed_url, "status": "invalid_feed"})
                continue
            feeds_fetched += 1
            feed_statuses.append({
                "url": feed_url,
                "status": "fetched" if entries else "empty",
                "items": len(entries),
            })
        else:
            feeds_fetched += 1
            feed_statuses[-1]["status"] = "homepage_fallback"
            feed_statuses[-1]["items"] = len(entries)

        for entry in entries:
            if created >= limit or created_this_feed >= per_feed_cap:
                break
            url = entry.get("url") or ""
            if not isinstance(url, str) or not host_allowed(url):
                skipped += 1
                continue
            canonical = canonicalize_url(url) or url
            native = entry.get("native_id") or hashlib.sha1(
                canonical.encode("utf-8")
            ).hexdigest()[:40]
            if native in known_ids or canonical in known_urls:
                duplicates += 1
                continue
            title = entry.get("title") or ""
            summary = entry.get("summary") or ""
            published = entry.get("published")
            published_at = published if isinstance(published, datetime) else None
            blob = f"{title}\n{summary}"
            if not should_fetch_new_item(
                published_at,
                newest_stored=newest,
                is_capability_deprecation=looks_like_deprecation(blob),
                now=now,
            ):
                outside_budget += 1
                continue

            pages_fetched += 1
            page_result = resolve_page(url)
            if len(page_result) == 3:
                page_text, method, page_status = page_result
            else:
                page_text, method = page_result
                page_status = "full_page" if page_text else "unavailable"
            if not page_text:
                page_fetch_failures += 1
            body = page_text or ""
            if fails_min_word_gate(body):
                if not fails_min_word_gate(blob):
                    body = blob
                    method = "rss"
                    page_status = "feed_summary_fallback"
                else:
                    skipped += 1
                    continue

            submission = Submission(
                text=f"{title}\n\n{body}".strip()[:12000],
                source_name=(
                    "Google Developer Forums"
                    if "discuss.google.dev" in feed_url
                    else "articles"
                ),
                source_url=url[:2000],
                submitted_by="article-feed",
                language="en",
            )
            if published_at is not None:
                submission.created_at = published_at
            db.add(submission)
            db.flush()
            record_source_provenance(
                db,
                submission,
                native_id=native,
                community="articles",
                source_url=url[:2000],
                canonical_url=canonical[:2000],
                source_created_at=published_at,
                metadata={
                    "feed": feed_url,
                    "publisher": urlparse(feed_url).hostname,
                    "item_kind": (
                        "forum" if "discuss.google.dev" in feed_url else "article"
                    ),
                    "fetch_method": method,
                    "page_fetch_status": (
                        page_status
                    ),
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            known_ids.add(native)
            known_urls.add(canonical)
            created += 1
            created_this_feed += 1
            submission_ids.append(submission.id)
            metadata_by_id[str(submission.id)] = {
                "feed": feed_url,
                "title": title,
                "item_kind": "article",
                "fetch_method": method,
            }

    db.commit()
    return {
        "source": "articles",
        "created": created,
        "new": created,
        "fetched": feeds_fetched,
        "feeds_requested": feeds_requested,
        "feed_failures": feed_failures,
        "feed_statuses": feed_statuses,
        "skipped": skipped + duplicates + outside_budget,
        "rejected": skipped + outside_budget,
        "duplicates_skipped": duplicates,
        "outside_budget": outside_budget,
        "pages_fetched": pages_fetched,
        "page_fetch_failures": page_fetch_failures,
        "submission_ids": submission_ids,
        "submission_metadata": metadata_by_id,
    }


@router.post("/articles")
def ingest_articles(
    limit: int = Query(default=8, ge=1, le=20),
    db: Session = Depends(get_db),
):
    return collect_articles(limit, db)
