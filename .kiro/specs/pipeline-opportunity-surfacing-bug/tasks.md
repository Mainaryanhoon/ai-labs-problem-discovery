# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Pipeline Opportunity Surfacing Bug
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bugs exist
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples demonstrating that KEEP_DEMAND items from non-demand-role sources silently zero the demand floor, that missing LLM fields immediately return "undetermined", and that in-scope sections without exact keyword matches block product_ready
  - **Scoped PBT Approach**: For deterministic bugs, scope the property to the concrete failing cases rather than fully random generation
  - **Property scope — three sub-conditions to exercise:**
    1. **Bug Condition A (Defect 1 — source-role mismatch):** Given a cluster whose only KEEP_DEMAND submissions come from `hacker_news` / `official_docs` / `articles` (non-demand-role sources per SOURCES.md), the system MUST expose `demand_metrics["role_blocked_keep_demand_count"] > 0` in the opportunity summary AND MUST NOT silently set that count to 0 with no diagnostic.
    2. **Bug Condition B (Defect 2 — offering angle undetermined):** Given an in-scope cluster (section="Analytics") where the first analysis row has non-null `leverage_on_offerings_1_to_5 >= 3` and `demand_strength >= 3` but is missing `failure_risk_line` (None or ""), calling `_offering_angle(components, analyses, in_scope=True)` MUST NOT return `"undetermined"` — it must return a score-based fallback angle.
    3. **Bug Condition C (Defect 3 — cites_known_offering gate too strict):** Given an in-scope cluster (section="Analytics") with valid demand-floor evidence but `smallest_first_version` / `problem_statement` that contain no KNOWN_OFFERING_MARKERS substring, `is_product_ready(...)` MUST NOT add the "Does not cite a known Tatvic offering" blocker when `in_scope_section is True`.
  - For Bug Condition A: create a test cluster using an in-memory SQLite DB (mirror pattern from `tests/test_opportunity_scoring.py`); attach 3 KEEP_DEMAND submissions with `source_name="hacker_news"`; call `build_opportunity_summary`; assert `result["demand_metrics"]["role_blocked_keep_demand_count"] >= 1`
  - For Bug Condition B: call `_offering_angle({"leverage_on_offerings": 3, "demand_strength": 3, "capability_readiness": 3}, [{"smallest_first_version": "A tool", "first_client_type": "analyst", "failure_risk_line": None, "leverage_on_offerings_1_to_5": 3}], in_scope=True)` and assert returned angle is NOT `"undetermined"`
  - For Bug Condition C: call `is_product_ready(top10_eligible=True, offering_angle="new_agent_or_offering", components={"leverage_on_offerings": 3, "demand_strength": 3, "capability_readiness": 3}, smallest_first_version="A pipeline monitoring dashboard", first_client_type="data engineer", failure_risk_line="Vendor native tool may ship", keep_demand_count=3, title="Pipeline monitoring", problem_texts=["pipeline monitoring solution for cloud data teams"])` and assert `product_ready is True` (no cites_known_offering blocker when in-scope section is provided via `offering_angle != "undetermined"`)
  - Run all three checks on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct — it proves the bugs exist)
  - Document counterexamples found to understand root cause
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Non-Buggy Baseline Behavior
  - **IMPORTANT**: Follow observation-first methodology — run UNFIXED code with non-buggy inputs first
  - **Inputs that must NOT trigger the fix paths (isBugCondition = False):**
    - A KEEP_DEMAND submission with `source_name="Reddit"` or `source_name="Stack Exchange:stackoverflow"` → must still count toward demand floor (not blocked)
    - `_offering_angle` called with all three text fields populated (`smallest`, `client`, `failure` all non-empty strings) → must still follow existing angle-selection logic unchanged
    - `is_product_ready` called with `offering_angle="upgrade_existing_offering"` where `cites_known_offering()` returns False → must still block with "Does not cite a known Tatvic offering" (regression 3.7)
    - Cluster with only 2 items / 1 author → `top10_eligible=False`, `product_ready=False` (regression 3.2)
    - Cluster with single source platform and score > 60 → score capped at 60 (regression 3.4)
    - DROP-coded submissions → excluded from eligible_records entirely (regression 3.3)
    - `product_ready=True` cluster with a numeric score → surfaces in section A or B of discovery report (regression 3.5)
    - Report written to `data/reports/opportunities_YYYY-MM-DD.md` path (regression 3.8)
  - Observe on UNFIXED code: confirm each of the above still behaves as expected before touching any fix code
  - Write property-based tests (using `hypothesis` or parameterize via pytest) capturing:
    - For all `source_name` values in `{"Reddit", "Stack Exchange:stackoverflow", "Stack Exchange:datascience"}`: demand floor count is unaffected (preserves 3.1)
    - For all complete `(smallest, client, failure)` triples: `_offering_angle` result is unchanged by the fix (preserves 3.7 indirectly)
    - The `upgrade_existing_offering` angle always requires `cites_known_offering()` (preserves 3.7)
    - Single-source cap ≤ 60 holds for all single-platform clusters (preserves 3.4)
  - Verify ALL preservation tests PASS on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [x] 3. Fix Defect 1 — source-role mismatch silently zeroes the demand floor diagnostic

  - [x] 3.1 Expose `role_blocked_keep_demand_count` in `demand_metrics` (app/main.py)
    - Locate `build_opportunity_summary()` in `app/main.py` (around the `demand_metrics` dict construction near the end of the function)
    - Confirm `role_blocked_keep_demand` list is already populated (it is, see existing code)
    - The existing `demand_metrics` dict already includes `"role_blocked_keep_demand_count": len(role_blocked_keep_demand)` — verify this key is present; if the read of bugfix.md says it was MISSING, add it now
    - Confirm `demand_role_item_count` is set to `len(eligible_records)` (already present — verify)
    - Ensure the `role_blocked_keep_demand_count` key propagates through to the opportunity summary dict that is returned and subsequently written to the discovery report
    - No change to the demand floor policy itself — Stack Exchange and Reddit remain the only demand sources
    - _Bug_Condition: isBugCondition(input) where all KEEP_DEMAND submissions have non-demand-role source names (hacker_news, official_docs, articles)_
    - _Expected_Behavior: demand_metrics["role_blocked_keep_demand_count"] reflects the actual count of KEEP_DEMAND items blocked by source-role policy_
    - _Preservation: Stack Exchange / Reddit KEEP_DEMAND items still count toward demand floor (3.1)_
    - _Requirements: 1.1, 2.1, 2.2_

  - [x] 3.2 Verify bug condition exploration test (property 1A) now passes
    - **Property 1: Expected Behavior** - Demand Floor Diagnostic Transparency
    - **IMPORTANT**: Re-run the SAME test from task 1 (Bug Condition A sub-check) — do NOT write a new test
    - The test from task 1 asserts `result["demand_metrics"]["role_blocked_keep_demand_count"] >= 1` for a cluster with only hacker_news KEEP_DEMAND items
    - Run that test and confirm it PASSES
    - **EXPECTED OUTCOME**: Test PASSES (confirms Defect 1 is fixed)
    - _Requirements: 2.1, 2.2_

  - [x] 3.3 Verify preservation tests still pass after Defect 1 fix
    - **Property 2: Preservation** - Demand Floor Baseline Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Specifically verify: Reddit / Stack Exchange KEEP_DEMAND items still increment demand floor (3.1), DROP codes still excluded (3.3)
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)

- [x] 4. Fix Defect 2 — `_offering_angle()` returns "undetermined" when any LLM field is absent

  - [x] 4.1 Implement score-based fallback in `_offering_angle()` (app/tatvic_scoring.py)
    - Locate `_offering_angle()` in `app/tatvic_scoring.py`
    - Current guard: `if not (smallest and client and failure): return "undetermined", smallest, client, failure`
    - Replace with a two-stage approach:
      1. If all three fields are present → proceed with the existing angle-selection logic unchanged
      2. If one or more fields are absent but `in_scope` is True → attempt a score-based fallback:
         - If `leverage >= 4` and `cites_known_offering(offering_blob, smallest or "")`: return `"upgrade_existing_offering"` with the available partial fields
         - Else if `demand >= 3` and `readiness >= 3` and `leverage >= 3`: return `"new_agent_or_offering"` with available partial fields
         - Else if native_solved check passes: return `"cheaper_delivery"` with available partial fields
         - Else fall through to `"undetermined"` (partial fields are insufficient even with scores)
    - The `upgrade_existing_offering` angle path MUST still require `cites_known_offering()` (preserves requirement 3.7)
    - The section must be in-scope (`in_scope=True`) for the fallback to activate — out-of-scope clusters still return `"undetermined"`
    - _Bug_Condition: isBugCondition(input) where in_scope=True AND (smallest is None OR client is None OR failure is None) AND leverage >= 3 AND demand >= 3_
    - _Expected_Behavior: _offering_angle returns a non-"undetermined" angle based on component scores when fields are partially available and section is in-scope_
    - _Preservation: upgrade_existing_offering still requires cites_known_offering() (3.7); complete-field path logic unchanged_
    - _Requirements: 1.3, 2.3, 3.7_

  - [x] 4.2 Verify bug condition exploration test (property 1B) now passes
    - **Property 1: Expected Behavior** - Offering Angle Score-Based Fallback
    - **IMPORTANT**: Re-run the SAME test from task 1 (Bug Condition B sub-check) — do NOT write a new test
    - The test asserts `_offering_angle(components, analyses_with_missing_failure, in_scope=True)` returns something other than `"undetermined"`
    - Run that test and confirm it PASSES
    - **EXPECTED OUTCOME**: Test PASSES (confirms Defect 2 is fixed)
    - _Requirements: 2.3_

  - [x] 4.3 Verify preservation tests still pass after Defect 2 fix
    - **Property 2: Preservation** - Offering Angle Complete-Field Path Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Specifically verify: complete `(smallest, client, failure)` path still produces same angle as before; `upgrade_existing_offering` still requires `cites_known_offering()`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)

- [x] 5. Fix Defect 3 — `cites_known_offering()` keyword gate is too strict

  - [x] 5.1 Relax the known-offering gate in `is_product_ready()` (app/tatvic_scoring.py)
    - Locate `is_product_ready()` in `app/tatvic_scoring.py`
    - Identify the blocker: `if not cites_known_offering(...): blockers.append("Does not cite a known Tatvic offering ...")`
    - Change the condition so the blocker is only added when BOTH of the following are true:
      1. `cites_known_offering(...)` returns False
      2. The cluster is NOT in an in-scope section (i.e., the section is `OUT_OF_SCOPE` or `None`)
    - In code terms: `if not cites_known_offering(...) and not in_scope_section: blockers.append(...)`
    - The `in_scope_section` boolean must be threaded into `is_product_ready()` — add it as a keyword argument (default `False` for backward compatibility)
    - Update the single call-site in `score_tatvic_opportunity()` to pass `in_scope_section=in_scope_section`
    - The `upgrade_existing_offering` angle MUST still unconditionally require `cites_known_offering()` (add a separate targeted check for that angle that ignores `in_scope_section`):
      - `if offering_angle == "upgrade_existing_offering" and not cites_known_offering(...): blockers.append("Upgrade angle requires a cited known offering.")`
    - _Bug_Condition: isBugCondition(input) where in_scope_section=True AND cites_known_offering()=False AND offering_angle != "upgrade_existing_offering"_
    - _Expected_Behavior: is_product_ready does not add the cites_known_offering blocker for in-scope sections with offering_angle other than upgrade_existing_offering_
    - _Preservation: upgrade_existing_offering still requires cites_known_offering() regardless of in_scope_section (3.7); out-of-scope clusters still blocked_
    - _Requirements: 1.4, 2.4, 3.7_

  - [x] 5.2 Verify bug condition exploration test (property 1C) now passes
    - **Property 1: Expected Behavior** - Known-Offering Gate Relaxed for In-Scope Sections
    - **IMPORTANT**: Re-run the SAME test from task 1 (Bug Condition C sub-check) — do NOT write a new test
    - The test asserts `is_product_ready(..., in_scope_section=True, offering_angle="new_agent_or_offering", ...)` returns `True` when `cites_known_offering()` would otherwise block it
    - Run that test and confirm it PASSES
    - **EXPECTED OUTCOME**: Test PASSES (confirms Defect 3 is fixed)
    - _Requirements: 2.4_

  - [x] 5.3 Verify preservation tests still pass after Defect 3 fix
    - **Property 2: Preservation** - Known-Offering Gate Unchanged for Upgrade Angle and Out-of-Scope
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Specifically verify: `upgrade_existing_offering` still requires `cites_known_offering()` (3.7); `OUT_OF_SCOPE` clusters still blocked without keyword match; all regression requirements 3.1–3.8 hold
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)

- [x] 6. Fix Defect 4 — deferred clustering count missing from discovery report

  - [x] 6.1 Add `deferred_clustering_count` to `stage_counts` in `run_pipeline()` (app/services/pipeline.py)
    - Locate the `clustering` dict and the `stage_counts["clustering"]` assembly block in `run_pipeline()`
    - After the clustering loop, compute: `deferred_count = max(0, len(eligible) - request.clustering_limit)`
    - This value is already used implicitly for `clustering_deferred`; make it explicit in the output
    - Add `"deferred_clustering_count": deferred_count` to the `stage_counts["clustering"]` dict alongside the existing keys (`considered`, `matched`, `created`, `duplicates`, `already_clustered`, `needs_review`, `failed`, `skipped`)
    - The `skipped` key already includes deferred items mixed with already-clustered and rejected items; `deferred_clustering_count` is a separate, additive diagnostic that isolates limit-triggered deferrals
    - The clustering limit itself must NOT be changed — it is a safety feature
    - _Bug_Condition: isBugCondition(input) where len(eligible) > request.clustering_limit (i.e., more KEEP items exist than the limit allows)_
    - _Expected_Behavior: stage_counts["clustering"]["deferred_clustering_count"] equals max(0, len(eligible) - clustering_limit)_
    - _Preservation: clustering_limit safety cap unchanged; existing skipped/considered/matched/created counts unchanged_
    - _Requirements: 1.5, 2.5_

  - [x] 6.2 Surface `deferred_clustering_count` in the discovery report markdown (app/services/discovery_report.py)
    - Locate `render_discovery_opportunities_markdown()` in `app/services/discovery_report.py`
    - Find the "Pipeline funnel (this run)" section where `clustering` stage counts are formatted
    - Add `"deferred_clustering_count"` to the list of clustering keys rendered in that section, e.g.:
      `"- Clustering: considered=X, matched=X, created=X, ..., deferred_clustering_count=X"`
    - This ensures operators can read the deferral count directly from `data/reports/opportunities_YYYY-MM-DD.md`
    - Report path policy unchanged — still writes to `data/reports/opportunities_YYYY-MM-DD.md` (preserves 3.8)
    - _Requirements: 2.5, 3.8_

  - [x] 6.3 Verify deferred_clustering_count appears in stage_counts and report
    - Write a targeted unit test (or extend existing `tests/test_pipeline.py`) that:
      1. Sets up a mock pipeline run where `len(eligible) > clustering_limit`
      2. Asserts `stage_counts["clustering"]["deferred_clustering_count"] == len(eligible) - clustering_limit`
    - Also verify the discovery report markdown contains the `deferred_clustering_count` key when stage_counts include it
    - _Requirements: 2.5_

- [x] 7. Checkpoint — Ensure all tests pass

  - [x] 7.1 Run the full test suite
    - Run `pytest` from the workspace root (use `--tb=short` for concise output)
    - Confirm zero test regressions across: `tests/test_tatvic_scoring.py`, `tests/test_opportunity_scoring.py`, `tests/test_pipeline.py`, `tests/test_discovery_report.py`, and all newly added tests
    - Address any failures before considering the fix complete

  - [x] 7.2 Verify the four existing regression tests that most directly guard the fix boundaries still pass
    - `test_missing_components_do_not_loosen_product_readiness` — ensures missing scores do not invent leverage (all-missing case still returns `product_ready=False`)
    - `test_single_source_platform_caps_at_60` — ensures single-source cap (3.4) holds
    - `test_worked_example_score_is_63_8` — ensures score arithmetic (v0.1) is unchanged
    - `test_complete_inputs_score_and_cap_unchanged` — regression guard for complete-input path
    - Ask the user if any unexpected failure arises that cannot be resolved by the fix scope above

  - [x] 7.3 Manual smoke check against the live database (optional, operator step)
    - Run the pipeline via `POST /api/pipeline/run` with a small source set
    - Open `data/reports/opportunities_YYYY-MM-DD.md` and confirm:
      - `role_blocked_keep_demand_count` appears in demand_metrics for any watchlist cluster
      - `deferred_clustering_count` appears in the clustering funnel section
      - Clusters with in-scope sections and partial LLM fields now have a non-"undetermined" offering angle
      - No previously-passing cluster has regressed to `product_ready=False`
    - This step does not block task completion; document findings for Khilav review
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 3.1–3.8_
