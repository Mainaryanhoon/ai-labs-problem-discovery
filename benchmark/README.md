# Offline benchmark v1

`app.evaluation` measures model predictions against human labels. It has no
provider, network, or database dependencies and applies no pass/fail
thresholds. Metrics are sample-specific; synthetic examples are smoke tests,
not evidence of real-world accuracy.

## Files

Use JSON Lines (`.jsonl`), one JSON object per line. Blank lines and lines
starting with `#` are ignored.

Labels require:

- `benchmark_version`: consistent, non-empty version string.
- `id`: unique string shared with the prediction record.
- `text`: the exact submission text used for judgment.
- `is_genuine_problem`: boolean; `signal_type` must agree with this label.
- `signal_type`: `pain_point`, `explicit_tool_request`,
  `workaround_or_manual_work`, `feature_gap`, `buying_signal`,
  `general_discussion`, or `noise`.
- `problem_id`: required for genuine problems; omit or use null for noise.
- Optional `purchase_intent`: `none`, `unclear`, `suggested`, or `explicit`.
- Optional `independence_group`: same value means the same real person or
  experience. Account, URL, or platform differences alone do not prove
  independence.
- Optional `pair_labels`: `[{ "other": "record-id", "label": "same" }]`.
  Pair labels may be `same`, `different`, or `uncertain`.

Prediction records use the same `id` and may include `is_genuine_problem`,
`signal_type`, `cluster_id` (null when unclustered),
`counted_as_independent`, `willingness_to_pay_signal`, and `evidence_quotes`.
They may include `prefilter_kept` (boolean) to measure prefilter recall on
labelled genuine problems. Missing decisions count as not retained and are
reported separately.
Optional ranking JSONL records use `problem_id`, `human_score`, and
`model_score` (null when unavailable).

## Labeling guidance

Label an actual difficulty, unmet need, costly workaround, manual process, or
specific frustration as a genuine problem. Product promotion, vague topic
discussion, jokes, hypotheticals, and unrelated content are not. A buying
intent label requires purchase or budget evidence from a potential customer,
not an author's product research or interest in building a product.

Use `uncertain` for pair judgments that reviewers cannot confidently resolve.
Keep a record of label changes and have a second reviewer label a subset.
Report disagreements and sample size alongside every metric.

## Synthetic smoke example

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m app.evaluation `
  benchmark\example_labels.synthetic.jsonl `
  benchmark\example_predictions.synthetic.jsonl `
  --ranking benchmark\example_ranking.synthetic.jsonl `
  --markdown benchmark-report.md `
  --rejected-sample-output rejected-review-sample.jsonl `
  --rejected-sample-size 25 `
  --sample-seed 7
```

The rejected sample contains only IDs and source text, not gold labels, for
blind review. It is sampled reproducibly from records marked
`prefilter_kept: false`; it does not access provider data or call a provider.
The example data is fabricated. Do not quote its metrics as product accuracy.
Build a reviewed, versioned set of real records before evaluating thresholds,
false positives/negatives, clustering merges, purchase-intent claims, or
ranking quality.
