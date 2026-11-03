"""Code-side hygiene gates and deterministic keep/drop stubs from RUBRIC.md."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

COLLECTION_WINDOW_DAYS = 60
FETCH_OVERLAP_DAYS = 3
MIN_WORDS = 12

_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")

REDDIT_TOPIC_KEYWORDS = re.compile(
    r"\b("
    r"ga4|google\s*analytics|gtm|tag\s*manager|tracking|attribution|"
    r"conversion|a/?b|experiment|bigquery|forecast|dashboard|report|data|"
    r"saas|mrr|churn|pricing|startup|founder|b2b|revenue|customer|"
    r"india|indian|d2c|margin|cac|rto|problem|pain|budget"
    r")\b",
    re.IGNORECASE,
)

NOISY_REDDIT_COMMUNITIES = frozenset({
    "startups",
    "smallbusiness",
    "sideproject",
    "startup_ideas",
    "entrepreneur",
})


def word_count(text: str | None) -> int:
    if not text:
        return 0
    return len(_WORD_RE.findall(text))


def fails_min_word_gate(text: str | None) -> bool:
    """RUBRIC Gate 1: fewer than 12 words → DROP_HYGIENE."""
    return word_count(text) < MIN_WORDS


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_within_collection_window(
    created_at: datetime | None,
    *,
    now: datetime | None = None,
    window_days: int = COLLECTION_WINDOW_DAYS,
) -> bool:
    """True when item date is unknown (pass through) or inside the cold-start window."""
    if created_at is None:
        return True
    clock = _as_utc(now or datetime.now(timezone.utc))
    return _as_utc(created_at) >= clock - timedelta(days=window_days)


def fetch_not_before(
    newest_stored: datetime | None,
    *,
    now: datetime | None = None,
    overlap_days: int = FETCH_OVERLAP_DAYS,
    bootstrap_days: int = COLLECTION_WINDOW_DAYS,
) -> datetime:
    """Earliest source time a new run should request.

    Cold start looks back `bootstrap_days`. Later runs start at the newest
    stored timestamp minus a short overlap so a late edit is not missed.
    """
    clock = _as_utc(now or datetime.now(timezone.utc))
    if newest_stored is None:
        return clock - timedelta(days=bootstrap_days)
    return _as_utc(newest_stored) - timedelta(days=overlap_days)


def should_fetch_new_item(
    created_at: datetime | None,
    *,
    newest_stored: datetime | None = None,
    is_capability_deprecation: bool = False,
    now: datetime | None = None,
) -> bool:
    """Ingest gate for a new raw item. Does not expire stored opportunities."""
    if is_capability_deprecation:
        return True
    if created_at is None:
        return True
    return _as_utc(created_at) >= fetch_not_before(newest_stored, now=now)


def reddit_topic_allowed(community: str | None, text: str) -> bool:
    """Noisy subreddits require a Tatvic-relevant keyword before model spend."""
    if not community:
        return True
    name = community.casefold().lstrip("r/")
    if name not in NOISY_REDDIT_COMMUNITIES:
        return True
    return bool(REDDIT_TOPIC_KEYWORDS.search(text or ""))


def looks_like_deprecation(text: str | None) -> bool:
    if not text:
        return False
    return bool(
        re.search(
            r"\b(deprecat|end of support|forced migration|sunset)\b",
            text,
            re.IGNORECASE,
        )
    )


def hygiene_drop_reason(
    *,
    text: str | None,
    created_at: datetime | None = None,
    is_capability_deprecation: bool = False,
    newest_stored: datetime | None = None,
    now: datetime | None = None,
) -> str | None:
    """Return DROP_HYGIENE when a new raw item fails Gate 1, else None.

    The date check is the fetch budget. It does not apply to stored clusters.
    """
    if fails_min_word_gate(text):
        return "DROP_HYGIENE"
    if not should_fetch_new_item(
        created_at,
        newest_stored=newest_stored,
        is_capability_deprecation=is_capability_deprecation,
        now=now,
    ):
        return "DROP_HYGIENE"
    return None


def classify_example_stub(text: str) -> str:
    """
    Deterministic stub for RUBRIC illustrative cases (calibration tests).
    Not a substitute for the LLM classifier on live items.
    """
    lowered = text.casefold()
    if "chrome" in lowered and "pdf" in lowered:
        return "DROP_CONSUMER"
    if "flink" in lowered or "truststore" in lowered:
        return "DROP_OUT_OF_SCOPE_TOPIC"
    if "central-bank" in lowered or (
        "circular" in lowered and "lender" in lowered
    ):
        return "DROP_REGULATORY"
    if "install ga4" in lowered and "beginner" in lowered:
        return "DROP_NO_PROBLEM"
    if "release note" in lowered or "in preview" in lowered:
        return "KEEP_CAPABILITY"
    if "reconcile" in lowered or "purchases don't match" in lowered:
        return "KEEP_DEMAND"
    if "bill doubled" in lowered or "scheduled queries" in lowered:
        return "KEEP_DEMAND"
    if "a/b test" in lowered or "backlog" in lowered:
        return "KEEP_DEMAND"
    if "forecast" in lowered and "sku" in lowered:
        return "KEEP_DEMAND"
    if "llm agent" in lowered or "bigquery joins" in lowered:
        return "KEEP_DEMAND"
    return "REVIEW"
