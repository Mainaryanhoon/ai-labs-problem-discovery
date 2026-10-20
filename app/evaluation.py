"""Offline metrics for versioned, human-labelled JSONL benchmarks.

This module is deterministic and has no provider, network, or database
dependencies. It reports measurements without asserting accuracy thresholds.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Iterable

ACTIONABLE = frozenset({
    "pain_point",
    "explicit_tool_request",
    "workaround_or_manual_work",
    "feature_gap",
    "buying_signal",
})
SIGNAL_TYPES = ACTIONABLE | {"general_discussion", "noise"}
PURCHASE_LEVELS = frozenset({"none", "unclear", "suggested", "explicit"})
BUYING_LEVELS = frozenset({"suggested", "explicit"})
PAIR_LABELS = frozenset({"same", "different", "uncertain"})


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records = []
    with Path(path).open(encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(value, dict):
                raise ValueError(
                    f"{path}:{line_number}: expected a JSON object"
                )
            records.append(value)
    return records


def _index(records: list[dict[str, Any]], name: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for number, record in enumerate(records, 1):
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError(f"{name} record {number}: id must be a non-empty string")
        if record_id in indexed:
            raise ValueError(f"{name} record {number}: duplicate id {record_id!r}")
        indexed[record_id] = record
    return indexed


def validate_labels(labels: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    versions = [record.get("benchmark_version") for record in labels]
    valid_versions = [
        value for value in versions if isinstance(value, str) and value.strip()
    ]
    known_versions = set(valid_versions)
    if (
        len(known_versions) > 1
        or len(valid_versions) != len(versions)
    ):
        errors.append("benchmark_version must be present and consistent")

    ids: set[str] = set()
    pair_claims: dict[tuple[str, str], str] = {}
    for number, record in enumerate(labels, 1):
        record_id = record.get("id")
        label = f"record {number} ({record_id!r})"
        if not isinstance(record_id, str) or not record_id.strip():
            errors.append(f"{label}: id must be a non-empty string")
        elif record_id in ids:
            errors.append(f"{label}: duplicate id")
        else:
            ids.add(record_id)
        if not isinstance(record.get("text"), str) or not record["text"].strip():
            errors.append(f"{label}: text must be non-empty")
        genuine = record.get("is_genuine_problem")
        if not isinstance(genuine, bool):
            errors.append(f"{label}: is_genuine_problem must be boolean")
            continue
        signal = record.get("signal_type")
        if not isinstance(signal, str) or signal not in SIGNAL_TYPES:
            errors.append(f"{label}: invalid signal_type")
        elif genuine != (signal in ACTIONABLE):
            errors.append(f"{label}: signal_type conflicts with genuine label")
        problem_id = record.get("problem_id")
        if genuine and (not isinstance(problem_id, str) or not problem_id.strip()):
            errors.append(f"{label}: genuine record needs a problem_id")
        if not genuine and problem_id is not None:
            errors.append(f"{label}: non-genuine record cannot have problem_id")
        purchase = record.get("purchase_intent", "none")
        if not isinstance(purchase, str) or purchase not in PURCHASE_LEVELS:
            errors.append(f"{label}: invalid purchase_intent")
        pairs = record.get("pair_labels", [])
        if not isinstance(pairs, list):
            errors.append(f"{label}: pair_labels must be a list")
            continue
        for pair in pairs:
            if (
                not isinstance(pair, dict)
                or not isinstance(record_id, str)
                or not isinstance(pair.get("other"), str)
                or pair.get("other") not in {
                    item.get("id") for item in labels
                    if isinstance(item.get("id"), str)
                }
                or pair.get("other") == record_id
                or pair.get("label") not in PAIR_LABELS
            ):
                errors.append(f"{label}: invalid pair label")
                continue
            pair_key = tuple(sorted((record_id, pair["other"])))
            previous = pair_claims.get(pair_key)
            if previous is not None and previous != pair["label"]:
                errors.append(f"{label}: conflicting pair label")
            pair_claims[pair_key] = pair["label"]
    return errors


def wilson_interval(
    successes: int, total: int, z: float = 1.96
) -> tuple[float, float] | None:
    if total <= 0:
        return None
    p = successes / total
    scale = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / scale
    margin = z * math.sqrt(
        p * (1 - p) / total + z * z / (4 * total * total)
    ) / scale
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    if denominator == 0:
        return {"value": None, "count": numerator, "of": denominator}
    interval = wilson_interval(numerator, denominator)
    return {
        "value": numerator / denominator,
        "count": numerator,
        "of": denominator,
        "wilson_95": list(interval) if interval else None,
    }


def _predicted_genuine(record: dict[str, Any]) -> bool:
    return (
        record.get("is_genuine_problem") is True
        and isinstance(record.get("signal_type"), str)
        and record.get("signal_type") in ACTIONABLE
    )


def classification_report(
    labels: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    gold, pred = _index(labels, "label"), _index(predictions, "prediction")
    matched = sorted(gold.keys() & pred.keys())
    tp = fp = fn = tn = 0
    for record_id in matched:
        actual = gold[record_id]["is_genuine_problem"]
        detected = _predicted_genuine(pred[record_id])
        if actual and detected:
            tp += 1
        elif detected:
            fp += 1
        elif actual:
            fn += 1
        else:
            tn += 1
    per_signal: dict[str, Any] = {}
    for signal in sorted(ACTIONABLE):
        signal_tp = sum(
            1 for i in matched
            if gold[i].get("signal_type") == signal
            and pred[i].get("signal_type") == signal
            and _predicted_genuine(pred[i])
        )
        signal_fp = sum(
            1 for i in matched
            if gold[i].get("signal_type") != signal
            and pred[i].get("signal_type") == signal
            and _predicted_genuine(pred[i])
        )
        signal_fn = sum(
            1 for i in matched
            if gold[i].get("signal_type") == signal
            and not (
                pred[i].get("signal_type") == signal
                and _predicted_genuine(pred[i])
            )
        )
        per_signal[signal] = {
            "support": sum(
                1 for i in matched if gold[i].get("signal_type") == signal
            ),
            "precision": _rate(signal_tp, signal_tp + signal_fp),
            "recall": _rate(signal_tp, signal_tp + signal_fn),
        }
    difficult = [i for i in matched if gold[i].get("difficult") is True]
    difficult_tp = difficult_fp = difficult_fn = difficult_tn = 0
    for record_id in difficult:
        actual = gold[record_id]["is_genuine_problem"]
        detected = _predicted_genuine(pred[record_id])
        if actual and detected:
            difficult_tp += 1
        elif detected:
            difficult_fp += 1
        elif actual:
            difficult_fn += 1
        else:
            difficult_tn += 1
    return {
        "labelled_records": len(gold),
        "matched_predictions": len(matched),
        "missing_predictions": sorted(gold.keys() - pred.keys()),
        "unknown_prediction_ids": sorted(pred.keys() - gold.keys()),
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "precision": _rate(tp, tp + fp),
        "recall": _rate(tp, tp + fn),
        "false_positive_rate": _rate(fp, fp + tn),
        "false_positive_ids": [
            i for i in matched
            if _predicted_genuine(pred[i]) and not gold[i]["is_genuine_problem"]
        ],
        "false_negative_ids": [
            i for i in matched
            if gold[i]["is_genuine_problem"] and not _predicted_genuine(pred[i])
        ],
        "per_signal_type": per_signal,
        "difficult_subset": {
            "n": len(difficult),
            "confusion": {
                "tp": difficult_tp,
                "fp": difficult_fp,
                "fn": difficult_fn,
                "tn": difficult_tn,
            },
            "precision": _rate(
                difficult_tp, difficult_tp + difficult_fp
            ),
            "recall": _rate(
                difficult_tp, difficult_tp + difficult_fn
            ),
        },
    }


def prefilter_report(
    labels: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Measure whether a pre-analysis filter retains labelled genuine needs."""
    gold, pred = _index(labels, "label"), _index(predictions, "prediction")
    genuine_ids = sorted(
        record_id
        for record_id, record in gold.items()
        if record.get("is_genuine_problem") is True
    )
    retained_ids = [
        record_id
        for record_id in genuine_ids
        if pred.get(record_id, {}).get("prefilter_kept") is True
    ]
    decisions = [
        record_id
        for record_id in genuine_ids
        if isinstance(pred.get(record_id, {}).get("prefilter_kept"), bool)
    ]
    return {
        "labelled_genuine_records": len(genuine_ids),
        "records_with_prefilter_decision": len(decisions),
        "retained_genuine_records": len(retained_ids),
        "filtered_or_unmeasured_genuine_ids": sorted(
            set(genuine_ids) - set(retained_ids)
        ),
        "recall": _rate(len(retained_ids), len(genuine_ids)),
        "warning": (
            "This is fixture/labeled-sample recall only; missing decisions "
            "count as not retained and do not establish population recall."
        ),
    }


def sample_rejected_posts(
    labels: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    *,
    sample_size: int,
    seed: int = 0,
) -> list[dict[str, str]]:
    """Select a reproducible, blind text sample of prefilter-rejected items."""
    if (
        isinstance(sample_size, bool)
        or not isinstance(sample_size, int)
        or sample_size < 0
    ):
        raise ValueError("sample_size must be a non-negative integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    gold, pred = _index(labels, "label"), _index(predictions, "prediction")
    candidates = [
        {"id": record_id, "text": record["text"]}
        for record_id, record in gold.items()
        if (
            isinstance(record.get("text"), str)
            and pred.get(record_id, {}).get("prefilter_kept") is False
        )
    ]
    candidates.sort(key=lambda item: item["id"])
    return random.Random(seed).sample(
        candidates,
        min(sample_size, len(candidates)),
    )


def _gold_pairs(labels: list[dict[str, Any]]) -> dict[frozenset[str], str]:
    pairs: dict[frozenset[str], str] = {}
    genuine = [r for r in labels if r.get("is_genuine_problem") is True]
    for left, right in combinations(genuine, 2):
        pairs[frozenset((left["id"], right["id"]))] = (
            "same" if left["problem_id"] == right["problem_id"] else "different"
        )
    for record in labels:
        for pair in record.get("pair_labels", []):
            pairs[frozenset((record["id"], pair["other"]))] = pair["label"]
    return pairs


def clustering_report(
    labels: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    pred = _index(predictions, "prediction")
    clusters: dict[str, Any] = {}
    for record_id, record in pred.items():
        cluster_id = record.get("cluster_id")
        if cluster_id is not None and isinstance(cluster_id, (str, int)):
            clusters[record_id] = cluster_id
    tp = fn = fp = tn = uncovered = uncertain_merged = uncertain_split = 0
    for pair, label in _gold_pairs(labels).items():
        left, right = tuple(pair)
        if left not in clusters or right not in clusters:
            uncovered += 1
            continue
        merged = clusters[left] == clusters[right]
        if label == "same":
            tp += int(merged)
            fn += int(not merged)
        elif label == "different":
            fp += int(merged)
            tn += int(not merged)
        elif merged:
            uncertain_merged += 1
        else:
            uncertain_split += 1
    problem_ids = {
        record["id"]: record.get("problem_id")
        for record in labels
        if record.get("is_genuine_problem") is True
    }
    cluster_problems: dict[Any, set[str]] = {}
    cluster_sizes: dict[Any, int] = {}
    for record_id, cluster_id in clusters.items():
        if record_id in problem_ids:
            cluster_problems.setdefault(cluster_id, set()).add(
                problem_ids[record_id]
            )
            cluster_sizes[cluster_id] = cluster_sizes.get(cluster_id, 0) + 1
    return {
        "pairs_evaluated": tp + fn + fp + tn,
        "pairs_without_predicted_cluster": uncovered,
        "uncertain_pairs_merged": uncertain_merged,
        "uncertain_pairs_split": uncertain_split,
        "false_merge_rate": _rate(fp, fp + tn),
        "missed_match_rate": _rate(fn, fn + tp),
        "pair_precision": _rate(tp, tp + fp),
        "pair_recall": _rate(tp, tp + fn),
        "clusters_mixing_distinct_problems": sorted(
            str(cluster_id)
            for cluster_id, problems in cluster_problems.items()
            if len(problems) > 1
        ),
        "singleton_clusters": sum(
            1 for size in cluster_sizes.values() if size == 1
        ),
    }


def evidence_integrity_report(
    labels: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    gold, pred = _index(labels, "label"), _index(predictions, "prediction")
    quote_count = traceable_count = 0
    untraceable_records = []
    for record_id, prediction in pred.items():
        if record_id not in gold:
            continue
        quotes = prediction.get("evidence_quotes") or []
        if not isinstance(quotes, list):
            untraceable_records.append(record_id)
            continue
        has_untraceable = False
        for quote in quotes:
            quote_count += 1
            if (
                isinstance(quote, str)
                and bool(quote.strip())
                and quote in gold[record_id]["text"]
            ):
                traceable_count += 1
            else:
                has_untraceable = True
        if has_untraceable:
            untraceable_records.append(record_id)
    return {
        "quotes_checked": quote_count,
        "traceable_quote_rate": _rate(traceable_count, quote_count),
        "records_with_untraceable_quotes": sorted(untraceable_records),
    }


def purchase_intent_report(
    labels: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    gold, pred = _index(labels, "label"), _index(predictions, "prediction")
    matched = sorted(gold.keys() & pred.keys())
    claimed = [
        i for i in matched
        if isinstance(pred[i].get("willingness_to_pay_signal"), str)
        and pred[i].get("willingness_to_pay_signal") in BUYING_LEVELS
    ]
    truth = [
        i for i in matched
        if isinstance(gold[i].get("purchase_intent", "none"), str)
        and gold[i].get("purchase_intent", "none") in BUYING_LEVELS
    ]
    true_positive = set(claimed) & set(truth)
    no_intent = [
        i for i in claimed
        if gold[i].get("purchase_intent", "none") == "none"
    ]
    no_quote = [
        i for i in claimed
        if not any(
            isinstance(quote, str)
            and quote
            and quote in gold[i]["text"]
            for quote in (
                pred[i].get("evidence_quotes")
                if isinstance(pred[i].get("evidence_quotes"), list)
                else []
            )
        )
    ]
    return {
        "claims_made": len(claimed),
        "claim_precision": _rate(len(true_positive), len(claimed)),
        "claim_recall": _rate(len(true_positive), len(truth)),
        "claims_without_traceable_quote": sorted(no_quote),
        "claims_on_records_labelled_no_intent": no_intent,
    }


def independence_report(
    labels: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    gold, pred = _index(labels, "label"), _index(predictions, "prediction")
    by_cluster: dict[Any, list[str]] = {}
    missing_groups = 0
    for record_id, prediction in pred.items():
        cluster_id = prediction.get("cluster_id")
        if (
            record_id in gold
            and cluster_id is not None
            and prediction.get("counted_as_independent") is True
            and isinstance(cluster_id, (str, int))
        ):
            by_cluster.setdefault(cluster_id, []).append(record_id)
    claimed = supportable = 0
    overclaimed = []
    for cluster_id, record_ids in by_cluster.items():
        groups = set()
        for record_id in record_ids:
            group = gold[record_id].get("independence_group")
            if not isinstance(group, str) or not group:
                missing_groups += 1
                continue
            groups.add(group)
        claimed += len(record_ids)
        supportable += len(groups)
        if len(groups) < len(record_ids):
            overclaimed.append(str(cluster_id))
    return {
        "claimed_independent_records": claimed,
        "supportable_independent_records": supportable,
        "overclaim_rate": _rate(claimed - supportable, claimed),
        "overclaimed_clusters": sorted(overclaimed),
        "records_missing_independence_group": missing_groups,
    }


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while (
            end + 1 < len(order)
            and values[order[end + 1]] == values[order[start]]
        ):
            end += 1
        average_rank = (start + end) / 2 + 1
        for position in range(start, end + 1):
            ranks[order[position]] = average_rank
        start = end + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rank_x, rank_y = _average_ranks(xs), _average_ranks(ys)
    mean_x = sum(rank_x) / len(rank_x)
    mean_y = sum(rank_y) / len(rank_y)
    covariance = sum(
        (x - mean_x) * (y - mean_y)
        for x, y in zip(rank_x, rank_y)
    )
    variance_x = sum((x - mean_x) ** 2 for x in rank_x)
    variance_y = sum((y - mean_y) ** 2 for y in rank_y)
    if variance_x == 0 or variance_y == 0:
        return None
    return covariance / math.sqrt(variance_x * variance_y)


def ranking_report(items: list[dict[str, Any]], top_k: int = 3) -> dict[str, Any]:
    if isinstance(top_k, bool) or top_k < 1:
        raise ValueError("top_k must be positive")
    usable = []
    unscored = []
    seen_problem_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("ranking items must be JSON objects")
        problem_id = item.get("problem_id")
        if not isinstance(problem_id, str) or not problem_id.strip():
            raise ValueError("problem_id must be a non-empty string")
        if problem_id in seen_problem_ids:
            raise ValueError(f"duplicate problem_id {problem_id!r}")
        seen_problem_ids.add(problem_id)
        human = item.get("human_score")
        model = item.get("model_score")
        if not isinstance(human, (int, float)) or isinstance(human, bool):
            continue
        if not math.isfinite(float(human)):
            continue
        if model is None:
            unscored.append(problem_id)
            continue
        if (
            not isinstance(model, (int, float))
            or isinstance(model, bool)
            or not math.isfinite(float(model))
        ):
            raise ValueError("model_score must be a finite number or null")
        usable.append((problem_id, float(human), float(model)))

    concordant = discordant = tied = 0
    for left, right in combinations(usable, 2):
        if left[1] == right[1]:
            continue
        if left[2] == right[2]:
            tied += 1
        elif (left[1] > right[1]) == (left[2] > right[2]):
            concordant += 1
        else:
            discordant += 1
    k = min(top_k, len(usable))
    top_human = {
        item[0] for item in sorted(usable, key=lambda row: -row[1])[:k]
    }
    top_model = {
        item[0] for item in sorted(usable, key=lambda row: -row[2])[:k]
    }
    return {
        "items_scored": len(usable),
        "items_unscored_by_model": sorted(unscored, key=str),
        "spearman_rho": spearman(
            [row[1] for row in usable],
            [row[2] for row in usable],
        ),
        "pairwise_agreement": _rate(concordant, concordant + discordant + tied),
        "pairs_tied_by_model": tied,
        "top_k": k,
        "top_k_overlap": _rate(len(top_human & top_model), k),
    }


def rating_sensitivity(
    score_fn: Callable[..., Any],
    analysis: dict[str, Any],
    fields: Iterable[str] = (
        "urgency_1_to_5",
        "frequency_signal_1_to_5",
        "evidence_strength_1_to_5",
    ),
    **score_kwargs: Any,
) -> dict[str, Any]:
    """Measure score changes when one supplied rating moves by one point."""
    base = score_fn(analysis, **score_kwargs).score
    changes: dict[str, float | None] = {}
    for field in fields:
        value = analysis.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        for delta in (-1, 1):
            adjusted = value + delta
            if not 1 <= adjusted <= 5:
                continue
            score = score_fn(
                {**analysis, field: adjusted},
                **score_kwargs,
            ).score
            changes[f"{field}{delta:+d}"] = (
                None
                if base is None or score is None
                else round(score - base, 4)
            )
    return {"base_score": base, "changes": changes}


def build_report(
    labels: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    ranking: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    errors = validate_labels(labels)
    if errors:
        raise ValueError("Invalid labels:\n- " + "\n- ".join(errors))
    # Duplicate or malformed prediction IDs must not be silently overwritten.
    _index(predictions, "prediction")
    return {
        "benchmark_versions": sorted({
            str(record["benchmark_version"]) for record in labels
        }),
        "sample_size": len(labels),
        "classification": classification_report(labels, predictions),
        "prefilter": prefilter_report(labels, predictions),
        "clustering": clustering_report(labels, predictions),
        "evidence_integrity": evidence_integrity_report(labels, predictions),
        "purchase_intent": purchase_intent_report(labels, predictions),
        "independence": independence_report(labels, predictions),
        "ranking": ranking_report(ranking) if ranking is not None else None,
        "warning": (
            "Metrics describe this labelled sample only; synthetic or small "
            "benchmarks do not establish real-world accuracy."
        ),
    }


def _markdown_report(report: dict[str, Any]) -> str:
    sections = ["# Offline benchmark report", ""]
    sections.append(f"Sample size: {report['sample_size']}")
    sections.append(f"Benchmark version(s): {', '.join(report['benchmark_versions'])}")
    sections.append("")
    sections.append(report["warning"])
    sections.append("")
    for name in (
        "classification",
        "prefilter",
        "clustering",
        "evidence_integrity",
        "purchase_intent",
        "independence",
        "ranking",
    ):
        value = report.get(name)
        if value is not None:
            sections.extend([f"## {name.replace('_', ' ').title()}", "", "```json"])
            sections.append(json.dumps(value, indent=2, sort_keys=True))
            sections.extend(["```", ""])
    return "\n".join(sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--ranking", type=Path)
    parser.add_argument("--json", dest="json_output", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument(
        "--rejected-sample-output",
        type=Path,
        help="Write a blind JSONL sample of records marked prefilter_kept=false.",
    )
    parser.add_argument("--rejected-sample-size", type=int, default=100)
    parser.add_argument("--sample-seed", type=int, default=0)
    args = parser.parse_args(argv)
    labels = load_jsonl(args.labels)
    predictions = load_jsonl(args.predictions)
    report = build_report(
        labels,
        predictions,
        load_jsonl(args.ranking) if args.ranking else None,
    )
    if args.rejected_sample_size < 0:
        parser.error("--rejected-sample-size must be non-negative")
    rendered_json = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_output:
        args.json_output.write_text(rendered_json, encoding="utf-8")
    else:
        print(rendered_json, end="")
    if args.markdown:
        args.markdown.write_text(_markdown_report(report), encoding="utf-8")
    if args.rejected_sample_output:
        sample = sample_rejected_posts(
            labels,
            predictions,
            sample_size=args.rejected_sample_size,
            seed=args.sample_seed,
        )
        args.rejected_sample_output.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in sample),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
