"""Normalize per-source collection outcomes for operators and reports."""

from __future__ import annotations

from typing import Any

# Default source wave plus explicit pilot-only YouTube reporting when requested.
DEFAULT_WAVE_SOURCES = (
    "official_docs",
    "stack_exchange",
    "hacker_news",
    "reddit",
)

# Capability context allowlisted in SOURCES.md §5 (not required in GOAL default).
ALLOWLISTED_CONTEXT_SOURCES = ("articles",)

PILOT_CONFIGURED_SOURCES = ("youtube",)


def normalize_source_yield(
    *,
    requested_sources: list[str],
    collection: dict[str, Any],
    include_default_wave: bool = True,
) -> dict[str, dict[str, Any]]:
    """Build an honest per-source yield map.

    Distinguishes ``not_requested`` from attempted success (including zero
    raw hits) and from failed fetches. Does not invent counts.
    """
    requested = list(requested_sources)
    requested_set = set(requested)
    out: dict[str, dict[str, Any]] = {}

    catalog: list[str] = []
    if include_default_wave:
        catalog.extend(DEFAULT_WAVE_SOURCES)
        catalog.extend(ALLOWLISTED_CONTEXT_SOURCES)
    for source in requested:
        if source not in catalog:
            catalog.append(source)

    for source in catalog:
        if source not in requested_set:
            out[source] = {
                "status": "not_requested",
                "attempted": False,
                "fetch_status": "not_requested",
            }
            continue
        outcome = collection.get(source)
        if not isinstance(outcome, dict):
            out[source] = {
                "status": "failed",
                "attempted": True,
                "fetch_status": "failed",
                "error_code": "collection_result_missing",
            }
            continue
        row = {
            key: value
            for key, value in outcome.items()
            if key not in {"submission_ids", "submission_metadata"}
            and isinstance(value, (int, float, bool, str, type(None)))
        }
        if isinstance(outcome.get("feed_statuses"), list):
            row["feed_statuses"] = [
                item
                for item in outcome["feed_statuses"]
                if isinstance(item, dict)
                and isinstance(item.get("url"), str)
                and isinstance(item.get("status"), str)
            ]
        if outcome.get("attempted") is False:
            row["status"] = outcome.get("status", "not_configured")
            row["attempted"] = False
            row["fetch_status"] = outcome.get(
                "fetch_status",
                "not_configured",
            )
            out[source] = row
            continue
        if outcome.get("status") == "failed":
            row["status"] = "failed"
            row["attempted"] = True
            row["fetch_status"] = "failed"
        else:
            row.setdefault("status", "completed")
            row["attempted"] = True
            row.setdefault(
                "fetch_status",
                outcome.get("fetch_status") or "succeeded",
            )
            if (
                row.get("fetched") == 0
                and row.get("created") == 0
                and "message" not in row
            ):
                row["message"] = (
                    "Fetch succeeded with zero items "
                    "(not a transport failure)."
                )
        out[source] = row
    return out
