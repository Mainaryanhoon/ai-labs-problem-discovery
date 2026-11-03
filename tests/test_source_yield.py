from app.services.source_yield import normalize_source_yield


def test_normalize_marks_default_wave_not_requested():
    result = normalize_source_yield(
        requested_sources=["hacker_news"],
        collection={
            "hacker_news": {
                "status": "completed",
                "fetched": 1,
                "created": 0,
                "duplicates_skipped": 1,
            }
        },
    )
    assert result["hacker_news"]["status"] == "completed"

    assert result["hacker_news"]["attempted"] is True
    assert result["hacker_news"]["fetched"] == 1
    assert result["official_docs"]["status"] == "not_requested"
    assert result["stack_exchange"]["fetch_status"] == "not_requested"
    assert result["reddit"]["attempted"] is False
    assert "x" not in result
    assert "youtube" not in result


def test_normalize_failed_vs_zero_success():
    result = normalize_source_yield(
        requested_sources=["hacker_news", "reddit"],
        collection={
            "hacker_news": {
                "status": "completed",
                "fetched": 0,
                "created": 0,
            },
            "reddit": {
                "status": "failed",
                "error_code": "provider_auth_error",
            },
        },
    )
    assert result["hacker_news"]["fetch_status"] == "succeeded"
    assert result["reddit"]["status"] == "failed"
    assert result["reddit"]["fetch_status"] == "failed"
    assert result["reddit"]["error_code"] == "provider_auth_error"


def test_normalize_preserves_not_configured_outcome():
    result = normalize_source_yield(
        requested_sources=["youtube"],
        collection={
            "youtube": {
                "attempted": False,
                "fetch_status": "not_configured",
                "created": 0,
            }
        },
        include_default_wave=False,
    )
    assert result["youtube"]["attempted"] is False
    assert result["youtube"]["fetch_status"] == "not_configured"
