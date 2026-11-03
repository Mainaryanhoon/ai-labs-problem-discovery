# Bugfix Requirements Document

## Introduction

The problem discovery pipeline collects evidence items across multiple sources (HN, Stack Exchange, Reddit, official docs, articles, YouTube) and is expected to produce a ranked list of 5–6 business opportunities. Instead, despite successfully collecting and analysing 30 items with KEEP_DEMAND=6 and KEEP_CAPABILITY=10 reason codes, the final report shows 0 commercial leads and 0 validated problem opportunities. Only 2 clusters appear in section C (watchlist), both marked `product_ready=False`.

Investigation of the codebase identified several compounding defects across the demand-floor eligibility filter (`build_opportunity_summary` in `app/main.py`), the offering-angle resolver (`_offering_angle` in `app/tatvic_scoring.py`), and the `is_product_ready` gate (`app/tatvic_scoring.py`). Together these defects cause every eligible cluster to fail the `product_ready` gate and be excluded from sections A and B of the discovery report, even when the collected and analysed evidence is substantive.

---

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a submission is classified `KEEP_DEMAND` by the LLM but comes from a source whose SOURCES.md role is not `demand` (e.g. Hacker News, official docs, articles), THEN the system silently drops it from the demand-floor candidate set (`eligible_records`) without surfacing any diagnostic or count in the final report's opportunity summary, causing the cluster's `demand_role_item_count` to count as zero even though KEEP_DEMAND items exist.

1.2 WHEN a cluster has zero demand-floor eligible records after source-role filtering, THEN the system sets `top10_eligible=False` and `product_ready=False`, placing the cluster in the watchlist regardless of how many KEEP_DEMAND or KEEP_CAPABILITY items are attached.

1.3 WHEN the LLM analysis does not return all three required text fields (`smallest_first_version`, `first_client_type`, `failure_risk_line`) as non-empty, non-N/A strings, THEN the system returns offering angle `"undetermined"` from `_offering_angle()`, blocking the `is_product_ready` gate even for clusters with sufficient demand-floor eligible items.

1.4 WHEN the LLM analysis fields (`smallest_first_version`, context blob, problem statement) do not contain one of the exact KNOWN_OFFERING_MARKERS keyword strings (e.g. `"bq agent"`, `"ga4"`, `"gtm"`, `"cro excalibur"`), THEN the system returns `cites_known_offering()=False` and the `is_product_ready` gate fails with the blocker "Does not cite a known Tatvic offering", regardless of whether the cluster is genuinely Tatvic-relevant.

1.5 WHEN very few submissions reach the clustering step (observed: `considered=2`, `matched=0`, `created=2`), THEN the system produces only 2 clusters total, preventing the discovery report from ever reaching the 5–10 ranked opportunity target even if individual items are correctly classified.

### Expected Behavior (Correct)

2.1 WHEN a submission is classified `KEEP_DEMAND` by the LLM but comes from a non-demand-role source (e.g. HN, official docs, articles), THEN the system SHALL record that item in `role_blocked_keep_demand` diagnostics (it already does), AND SHALL surface an accurate count of blocked KEEP_DEMAND items in the `demand_metrics` dict so operators can see exactly why demand counts are low, AND the cluster SHALL still be visible in the watchlist with a clear "source-role mismatch" explanation.

2.2 WHEN a cluster has zero demand-floor eligible records solely because all KEEP_DEMAND items come from non-demand-role sources, THEN the system SHALL output a discovery report that honestly states the demand floor was not met due to source-role policy (not due to absence of analysis), and SHALL include the cluster in the watchlist section C with `role_blocked_keep_demand_count > 0` clearly shown.

2.3 WHEN the LLM analysis does not return all three offering-angle fields (`smallest_first_version`, `first_client_type`, `failure_risk_line`), THEN the system SHALL fall back to a best-effort offering angle based on the available `leverage_on_offerings` and `demand_strength` component scores, rather than immediately returning `"undetermined"` and blocking product-readiness for clusters that otherwise meet the evidence floor.

2.4 WHEN a cluster's section and problem statement are clearly within a Tatvic delivery domain (Analytics, Cloud and Data Engineering, Data Science and AI/ML, Agentic AI, or CRO) but the LLM output fields do not literally contain one of the KNOWN_OFFERING_MARKERS strings, THEN the system SHALL NOT block `product_ready` solely on the `cites_known_offering()` keyword check; the in-scope section SHALL be sufficient to satisfy the "known offering" gate, or the gate SHALL be relaxed to match section membership in addition to keyword strings.

2.5 WHEN the pipeline runs with `clustering_limit` items eligible for clustering and more than `clustering_limit` items are pending, THEN the system SHALL log the number of items deferred and the discovery report SHALL state how many eligible items were not yet clustered, so operators can re-run or increase the limit to surface all opportunities.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a submission comes from Stack Exchange or Reddit (genuine demand-role sources per SOURCES.md) and is classified `KEEP_DEMAND`, THEN the system SHALL CONTINUE TO count it toward the demand floor (`demand_role_item_count`) and include it in `eligible_records` for scoring.

3.2 WHEN a cluster has fewer than 3 independent demand-floor items from fewer than 2 distinct authors, THEN the system SHALL CONTINUE TO exclude it from sections A and B (commercial leads / validated problem opportunities) and place it in the watchlist only.

3.3 WHEN a submission is classified with a DROP code (DROP_SPAM, DROP_OUT_OF_SCOPE_TOPIC, DROP_CONSUMER, etc.), THEN the system SHALL CONTINUE TO exclude it from clustering and scoring entirely.

3.4 WHEN a cluster's evidence comes from only one source platform, THEN the system SHALL CONTINUE TO cap its final score at 60 per RUBRIC §6.6.

3.5 WHEN a cluster is marked `product_ready=True` and has a numeric score, THEN the system SHALL CONTINUE TO surface it in section A (if `is_commercial_lead=True`) or section B (if `is_validated_problem_opportunity=True`) of the discovery report.

3.6 WHEN a submission passes all demand-floor gates, THEN the system SHALL CONTINUE TO require `top10_eligible` (≥3 items, ≥2 authors, in-scope section) before marking a cluster `product_ready`.

3.7 WHEN the `cites_known_offering()` check returns False for an offering angle of `"upgrade_existing_offering"`, THEN the system SHALL CONTINUE TO require that specific check (upgrade angle is only valid against a named existing offering).

3.8 WHEN the pipeline report is written, THEN the system SHALL CONTINUE TO write it to `data/reports/opportunities_YYYY-MM-DD.md` and SHALL CONTINUE TO never pad the ranked list with opportunities that do not meet the evidence floor.
