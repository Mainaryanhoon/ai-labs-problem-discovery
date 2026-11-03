"""Rubric v0.3-leads. Commercial demand is the largest single weight."""
from __future__ import annotations

import re

WEIGHT_VERSION = "v0.3-leads"

WEIGHTS: dict[str, int] = {
    "commercial_demand": 35,
    "india_relevance": 15,
    "problem_significance": 15,
    "buyer_path": 10,
    "why_now": 10,
    "tatvic_fit": 10,
    "evidence_quality": 5,
}
assert sum(WEIGHTS.values()) == 100

MIN_LEAD_SCORE = 45.0
MIN_INDIA_RELEVANCE = 3          # below this a lead goes to the watchlist
RESURFACE_SCORE_DELTA = 10.0     # re-show an old idea only if score moved this much

INDIA_MARKERS = (
    "india", "indian", "inr", "crore", "lakh", "bengaluru", "bangalore", "mumbai", "delhi",
    "gurugram", "gurgaon", "hyderabad", "chennai", "pune", "kolkata", "ahmedabad", "noida",
    "gst", "upi", "rbi", "sebi", "msme", "d2c", "bharat", "tier-2", "tier 2", "tier-3", "ondc",
    "rupee", "rupees",
)
_INDIA_RE = re.compile(
    r"(?<![a-z0-9])(" + "|".join(re.escape(m) for m in INDIA_MARKERS) + r")(?![a-z0-9])", re.I
)
_RUPEE_RE = re.compile(r"₹|\brs\.\s?\d")


def india_marker_count(text: str) -> int:
    t = text or ""
    found = {m.group(1).casefold() for m in _INDIA_RE.finditer(t)}
    return len(found) + (1 if _RUPEE_RE.search(t) else 0)


def india_cap_from_text(text: str) -> int:
    """Deterministic ceiling on model-claimed India relevance (1-5)."""
    n = india_marker_count(text)
    if n == 0:
        return 1
    if n == 1:
        return 2
    if n == 2:
        return 3
    if n <= 4:
        return 4
    return 5


def _bound(v) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 1
    return max(1, min(5, int(round(v))))


def compute_score(components: dict[str, int], feedback_adjustment: float = 0.0) -> tuple[float, dict[str, float]]:
    contributions: dict[str, float] = {}
    total = 0.0
    for key, weight in WEIGHTS.items():
        part = weight * (_bound(components.get(key)) - 1) / 4
        contributions[key] = round(part, 2)
        total += part
    total = max(0.0, min(100.0, total + feedback_adjustment))
    return round(total, 1), contributions


_STOP = {"the", "a", "an", "of", "for", "to", "in", "on", "and", "or", "with", "by", "at", "is", "are", "how", "why"}


def title_tokens(title: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", (title or "").casefold())
    return [t for t in toks if t not in _STOP and len(t) > 2]


def jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
