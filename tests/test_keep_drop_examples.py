"""Tatvic keep/drop examples as deterministic filter helpers for calibration."""

from __future__ import annotations

import pytest

from app.docs_filter_helpers import classify_example_stub


@pytest.mark.parametrize(
    ("text", "expected_code"),
    [
        ("How do I rename a PDF filename in Chrome?", "DROP_CONSUMER"),
        (
            "How do I put a Flink truststore inside my JAR?",
            "DROP_OUT_OF_SCOPE_TOPIC",
        ),
        (
            "GA4 purchases don't match store orders; we reconcile weekly by hand",
            "KEEP_DEMAND",
        ),
        (
            "BigQuery bill doubled after adding scheduled queries",
            "KEEP_DEMAND",
        ),
        ("How do I install GA4 as a beginner?", "DROP_NO_PROBLEM"),
        (
            "Release note: new forecasting function in preview in BigQuery",
            "KEEP_CAPABILITY",
        ),
        (
            "Turning our A/B test backlog into live variants takes weeks",
            "KEEP_DEMAND",
        ),
        (
            "We need daily demand forecasts for thousands of SKUs without a model per SKU",
            "KEEP_DEMAND",
        ),
        (
            "How do lenders interpret a new central-bank circular?",
            "DROP_REGULATORY",
        ),
    ],
)
def test_keep_drop_stub_matches_policy_examples(text, expected_code):
    assert classify_example_stub(text) == expected_code
