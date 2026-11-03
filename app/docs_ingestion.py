"""Official documentation / release-notes capability collector."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.clustering import canonicalize_url
from app.db import get_db
from app.docs_filter_helpers import (
    fetch_not_before,
    looks_like_deprecation,
    should_fetch_new_item,
)
from app.ingestion import clean_text, parse_source_datetime
from app.models import Submission
from app.provenance import (
    known_native_ids,
    known_observation_urls,
    newest_source_created_at,
    record_source_provenance,
)
from app.source_policy import policy_docs_feeds

router = APIRouter(prefix="/api/ingestion", tags=["Ingestion"])

# Used only on the consolidated GCP feed (titles are often just dates).
RELEVANT = re.compile(
    r"bigquery|vertex|dataplex|composer|dataform|looker|gemini|"
    r"forecast|anomaly|tag manager|analytics|ga4|experiment|"
    r"\bai\.|translate|rag|embedding|ml\b",
    re.IGNORECASE,
)

def configured_feeds() -> list[tuple[str, bool]]:
    """Return exact release-note URLs listed in the authoritative policy."""
    result = []
    for url in policy_docs_feeds():
        is_bigquery_feed = "bigquery-release-notes" in url
        result.append((url, is_bigquery_feed))
    return result


def _parse_atom_entries(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    ns = {"": "http://www.w3.org/2005/Atom"}
    entries = []
    for entry in root.findall("entry", ns):
        title = (entry.findtext("title", default="", namespaces=ns) or "").strip()
        summary = (
            entry.findtext("summary", default="", namespaces=ns)
            or entry.findtext("content", default="", namespaces=ns)
            or ""
        )
        link_el = entry.find("link", ns)
        href = link_el.get("href") if link_el is not None else None
        updated = entry.findtext("updated", default="", namespaces=ns) or ""
        entry_id = entry.findtext("id", default="", namespaces=ns) or href or title
        entries.append({
            "title": title,
            "summary": clean_text(summary),
            "url": href,
            "updated": updated,
            "id": entry_id,
        })
    return entries


def collect_official_docs(
    limit: int = 25,
    db: Session | None = None,
) -> dict[str, Any]:
    if db is None:
        raise HTTPException(status_code=500, detail="Database session required.")

    feeds = configured_feeds()
    if not feeds:
        return {
            "source": "official_docs",
            "attempted": False,
            "fetch_status": "not_configured",
            "created": 0,
            "submission_ids": [],
            "submission_metadata": {},
        }

    known_ids = known_native_ids(db, "official_docs")
    known_urls = known_observation_urls(db, "official_docs")
    newest = newest_source_created_at(db, "official_docs")
    not_before = fetch_not_before(newest)
    created = 0
    skipped = 0
    irrelevant = 0
    duplicates = 0
    outside_budget = 0
    feed_failures = 0
    feed_statuses: list[dict[str, Any]] = []
    submission_ids: list[int] = []
    metadata_by_id: dict[str, dict[str, Any]] = {}

    for feed_url, keep_all in feeds:
        if created >= limit:
            break
        try:
            response = httpx.get(feed_url, timeout=30, follow_redirects=False)
            response.raise_for_status()
            entries = _parse_atom_entries(response.text)
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
            continue
        except httpx.HTTPError:
            skipped += 1
            feed_failures += 1
            feed_statuses.append({"url": feed_url, "status": "unavailable"})
            continue
        except ET.ParseError:
            skipped += 1
            feed_failures += 1
            feed_statuses.append({"url": feed_url, "status": "invalid_feed"})
            continue
        feed_statuses.append({
            "url": feed_url,
            "status": "fetched" if entries else "empty",
            "items": len(entries),
        })

        for entry in entries:
            if created >= limit:
                break
            blob = f"{entry['title']} {entry['summary']}"
            if not keep_all and not RELEVANT.search(blob):
                irrelevant += 1
                continue
            native = entry["id"] or hashlib.sha1(
                blob.encode("utf-8")
            ).hexdigest()[:40]
            url = entry.get("url") or feed_url
            if not isinstance(url, str) or not url.strip():
                url = feed_url
            # Release-note Atom links share one path and differ only by fragment /
            # entry id. canonicalize_url strips fragments, so keep a stable unique key.
            base = canonicalize_url(url.split("#", 1)[0]) or url.split("#", 1)[0]
            canonical = f"{base}#{native}"
            if native in known_ids or canonical in known_urls:
                duplicates += 1
                continue

            published = parse_source_datetime(entry.get("updated"))
            if not should_fetch_new_item(
                published,
                newest_stored=newest,
                is_capability_deprecation=looks_like_deprecation(blob),
            ):
                outside_budget += 1
                continue

            # Prefer a readable statement when Atom title is only a date.
            title = entry["title"]
            summary = entry["summary"]
            if re.fullmatch(r"[A-Za-z]+ \d{1,2}, \d{4}", title or "") and summary:
                text = f"BigQuery / GCP release ({title}): {summary}".strip()
            else:
                text = f"{title}\n\n{summary}".strip()
            if len(text) < 30:
                irrelevant += 1
                continue

            submission = Submission(
                text=text[:12000],
                source_name="official_docs",
                source_url=url,
                submitted_by="google-cloud-release-notes",
                language="en",
            )
            if published is not None:
                submission.created_at = published
            db.add(submission)
            db.flush()
            record_source_provenance(
                db,
                submission,
                native_id=str(native)[:120],
                community="gcp-release-notes",
                source_url=url,
                canonical_url=canonical,
                source_created_at=published,
                metadata={
                    "feed": feed_url,
                    "item_kind": "capability",
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            known_ids.add(str(native)[:120])
            known_urls.add(canonical)
            created += 1
            submission_ids.append(submission.id)
            metadata_by_id[str(submission.id)] = {
                "feed": feed_url,
                "title": title,
                "item_kind": "capability",
            }

    db.commit()
    return {
        "source": "official_docs",
        "created": created,
        "skipped": skipped + irrelevant + duplicates,
        "irrelevant_skipped": irrelevant,
        "duplicates_skipped": duplicates,
        "outside_budget": outside_budget,
        "feed_failures": feed_failures,
        "feed_statuses": feed_statuses,
        "fetch_not_before": not_before.isoformat(),
        "submission_ids": submission_ids,
        "submission_metadata": metadata_by_id,
    }


@router.post("/official-docs")
def ingest_official_docs(
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return collect_official_docs(limit, db)
