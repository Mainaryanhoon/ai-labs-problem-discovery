import json

import pytest

from app.evaluation import (
    build_report,
    clustering_report,
    evidence_integrity_report,
    load_jsonl,
    ranking_report,
    rating_sensitivity,
    sample_rejected_posts,
    spearman,
    validate_labels,
    wilson_interval,
)
from app.ranking import score_opportunity


def _labels():
    return [
        {
            "benchmark_version": "test-v1",
            "id": "a",
            "text": "Manual invoicing takes hours each week.",
            "is_genuine_problem": True,
            "signal_type": "workaround_or_manual_work",
            "problem_id": "invoices",
            "purchase_intent": "none",
            "independence_group": "person-1",
            "pair_labels": [{"other": "b", "label": "same"}],
        },
        {
            "benchmark_version": "test-v1",
            "id": "b",
            "text": "I struggle to reconcile invoices manually.",
            "is_genuine_problem": True,
            "signal_type": "pain_point",
            "problem_id": "invoices",
            "purchase_intent": "explicit",
            "independence_group": "person-2",
        },
        {
            "benchmark_version": "test-v1",
            "id": "c",
            "text": "Our product is available now.",
            "is_genuine_problem": False,
            "signal_type": "noise",
            "problem_id": None,
            "purchase_intent": "none",
            "independence_group": "person-1",
        },
    ]


def _predictions():
    return [
        {
            "id": "a",
            "is_genuine_problem": True,
            "signal_type": "workaround_or_manual_work",
            "cluster_id": "cluster-a",
            "counted_as_independent": True,
            "willingness_to_pay_signal": "none",
            "evidence_quotes": ["takes hours each week"],
            "prefilter_kept": True,
        },
        {
            "id": "b",
            "is_genuine_problem": True,
            "signal_type": "pain_point",
            "cluster_id": "cluster-a",
            "counted_as_independent": True,
            "willingness_to_pay_signal": "explicit",
            "evidence_quotes": ["reconcile invoices manually"],
            "prefilter_kept": False,
        },
        {
            "id": "c",
            "is_genuine_problem": True,
            "signal_type": "pain_point",
            "cluster_id": "cluster-a",
            "counted_as_independent": True,
            "willingness_to_pay_signal": "explicit",
            "evidence_quotes": ["not in source"],
            "prefilter_kept": False,
        },
    ]


def test_build_report_measures_errors_and_preserves_unknown_denominators():
    report = build_report(
        _labels(),
        _predictions(),
        ranking=[
            {"problem_id": "invoices", "human_score": 5, "model_score": 70},
            {"problem_id": "other", "human_score": 1, "model_score": 20},
        ],
    )

    classification = report["classification"]
    assert classification["confusion"] == {"tp": 2, "fp": 1, "fn": 0, "tn": 0}
    assert classification["precision"]["value"] == pytest.approx(2 / 3)
    assert report["clustering"]["false_merge_rate"]["value"] is None
    assert report["purchase_intent"]["claims_on_records_labelled_no_intent"] == ["c"]
    assert report["independence"]["overclaim_rate"]["value"] == pytest.approx(1 / 3)
    assert report["ranking"]["top_k_overlap"]["value"] == 1.0
    assert report["ranking"]["spearman_rho"] == 1.0
    assert report["evidence_integrity"]["traceable_quote_rate"]["value"] == pytest.approx(2 / 3)
    assert report["prefilter"]["recall"]["value"] == pytest.approx(1 / 2)
    assert "do not establish real-world accuracy" in report["warning"]


def test_rejected_post_sampling_is_blind_repeatable_and_bounded():
    labels = _labels()
    predictions = _predictions()

    first = sample_rejected_posts(
        labels,
        predictions,
        sample_size=10,
        seed=7,
    )
    second = sample_rejected_posts(
        labels,
        predictions,
        sample_size=10,
        seed=7,
    )

    assert first == second
    assert {item["id"] for item in first} == {"b", "c"}
    assert all(set(item) == {"id", "text"} for item in first)
    assert sample_rejected_posts(
        labels,
        predictions,
        sample_size=0,
    ) == []
    with pytest.raises(ValueError, match="sample_size"):
        sample_rejected_posts(
            labels,
            predictions,
            sample_size=-1,
        )


def test_label_validation_rejects_inconsistent_and_malformed_fields():
    labels = _labels()
    labels[0]["pair_labels"] = [
        {"other": "b", "label": "same"},
        {"other": [], "label": "same"},
    ]
    labels[1]["pair_labels"] = [{"other": "a", "label": "different"}]
    labels[1]["signal_type"] = ["pain_point"]
    labels[2]["benchmark_version"] = {"unexpected": "object"}

    errors = validate_labels(labels)

    assert any("invalid pair label" in error for error in errors)
    assert any("invalid signal_type" in error for error in errors)
    assert any("benchmark_version" in error for error in errors)
    assert any("conflicting pair label" in error for error in errors)


def test_duplicate_prediction_ids_are_rejected_instead_of_overwritten():
    with pytest.raises(ValueError, match="duplicate id"):
        build_report(_labels(), [{"id": "a"}, {"id": "a"}])


def test_malformed_prediction_fields_are_reported_without_crashing():
    predictions = _predictions()
    predictions[0]["signal_type"] = []
    predictions[0]["willingness_to_pay_signal"] = []
    predictions[0]["evidence_quotes"] = "not-a-list"

    report = build_report(_labels(), predictions)

    assert report["classification"]["confusion"]["fn"] == 1
    assert "a" in report["evidence_integrity"]["records_with_untraceable_quotes"]


def test_clustering_reports_missing_predictions_and_unhashable_cluster_as_uncovered():
    report = clustering_report(
        _labels(),
        [{"id": "a", "cluster_id": {"bad": "cluster"}}],
    )

    assert report["pairs_without_predicted_cluster"] > 0


def test_ranking_rejects_invalid_model_scores_and_zero_k():
    with pytest.raises(ValueError, match="model_score"):
        ranking_report([
            {"problem_id": "x", "human_score": 1, "model_score": "unknown"}
        ])
    with pytest.raises(ValueError, match="top_k"):
        ranking_report([], top_k=0)
    with pytest.raises(ValueError, match="duplicate problem_id"):
        ranking_report([
            {"problem_id": "same", "human_score": 1, "model_score": 1},
            {"problem_id": "same", "human_score": 2, "model_score": 2},
        ])


def test_spearman_handles_ties_and_undefined_cases():
    assert spearman([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert spearman([1, 2, 3], [30, 20, 10]) == pytest.approx(-1.0)
    assert spearman([1, 1, 2], [10, 10, 20]) == pytest.approx(1.0)
    assert spearman([1, 1], [2, 3]) is None


def test_wilson_interval_and_score_sensitivity_are_measured_without_thresholds():
    interval = wilson_interval(2, 2)
    assert interval is not None
    assert interval[0] < 0.5
    assert interval[1] == pytest.approx(1.0)
    assert wilson_interval(0, 0) is None

    result = rating_sensitivity(
        score_opportunity,
        {
            "is_genuine_problem": True,
            "signal_type": "pain_point",
            "urgency_1_to_5": 3,
            "frequency_signal_1_to_5": 3,
            "evidence_strength_1_to_5": 3,
            "willingness_to_pay_signal": "none",
            "software_buildability": "likely",
            "evidence_quotes": [],
        },
    )
    assert result["base_score"] is not None
    assert result["changes"]["urgency_1_to_5+1"] >= 0
    assert result["changes"]["urgency_1_to_5-1"] <= 0


def test_jsonl_loader_rejects_non_object_and_invalid_json(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps(["not", "an", "object"]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a JSON object"):
        load_jsonl(path)

    path.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_jsonl(path)
