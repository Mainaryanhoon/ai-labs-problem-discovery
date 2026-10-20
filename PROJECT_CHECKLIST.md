# Project checklist

Status reflects local code and deterministic test validation. Live provider
compatibility and human-labeled quality remain separate, unverified gates.

## V1 backend roadmap (Phases 0–5)

| Phase | Status | Verified scope and limits |
|---|---|---|
| 0 — Health/build and local verification | PASS | `/health` identifies service/version; README and operator docs give Windows CMD health/OpenAPI verification commands. The existing backend port was occupied and left untouched; only the Vite frontend was started briefly for a fixture-mocked preview. |
| 1 — Fixture-driven end-to-end validation | PASS | Mocked pipeline tests cover collection → analysis → clustering → opportunity retrieval, stale-record isolation, duplicate runs, persisted run history, and partial/failure reporting. No providers are called by these tests. |
| 2 — Source quality and provenance | PASS (local) | Reddit/X actor contracts, post-only filtering, retweet safeguards, native-ID idempotence, source timestamps, cross-source duplicate observations, prefilter recall reporting, and blind rejected-text sampling have deterministic coverage. Provider output/account behavior remains live-unverified. |
| 3 — Categorization and cluster evaluation | PASS (local) | AI categories are schema-restricted; opportunity retrieval filters by category; offline labeled-pair clustering metrics are available. Synthetic fixtures do not establish model accuracy or threshold calibration. |
| 4 — Explainable scoring | PASS (local) | Software buildability gates numeric eligibility; source-timestamped 7-day growth is explained and bounded; score sensitivity and invariant tests are deterministic. Weights remain heuristic, not calibrated. |
| 5 — Weekly ranking history | PASS (local) | UTC-week snapshots are immutable/idempotent, historical snapshots are queryable, and weekly rank/score trends are exposed and tested. Snapshot creation is operator-triggered; no scheduler is included. |
| 6 — UI | PASS (local) | React/TypeScript dashboard covers discover, opportunities/detail, source usage, run history and explicit run submission. Five frontend tests, TypeScript/build checks, and a browser preview with mocked API fixtures passed. No provider or real database request was made by the preview; live API/demo verification remains operator-controlled. |

| Phase | Status | Notes |
|---|---|---|
| A — Structured problem analysis | IMPLEMENTED LOCALLY | Conservative extraction rules, canonical categories, software-buildability classification, schema checks, quote traceability, bounded transient retries, and rate-limit handling have deterministic tests. Historical live-test claims are not evidence of current provider compatibility. |
| B — Clustering and deduplication | IMPLEMENTED LOCALLY; HUMAN-LABELED EVALUATION PENDING | Unicode-aware exact text matching, conservative URL canonicalization, stable duplicate selection, idempotent attachment, embedding validation, deterministic tie-breaking, and unverified-by-default evidence are covered by isolated deterministic tests. Similarity thresholds remain provisional; no human-labeled clustering benchmark has been run. |
| C — Opportunity scoring | IMPLEMENTED LOCALLY; HUMAN-LABELED EVALUATION PENDING | Conservative eligibility, strict rating validation, duplicate-resistant aggregation, traceable-quote checks for commercial bonuses, and transparent evidence/source counts are covered by deterministic tests. Verified-independence bonuses remain disabled; the heuristic is not calibrated. No human-reviewed ranking benchmark has been run. |
| D — Source ingestion | IMPLEMENTED LOCALLY; LIVE-PROVIDER VALIDATION PENDING | Hacker News and Stack Exchange request validation, bounded transient GET retries, timestamp/author mapping, malformed-item handling, canonical URL/text deduplication, source-native ID capture, and repeat-run idempotence are covered by mocked tests. Reddit uses `searchQueries` for search and `startUrls` for community listings, caps total output and locally enforces allocated per-community ceilings; only `itemType=post` is saved. X uses the configured `apidojo~tweet-scraper` actor, one search term and exactly 50 requested results, accepts documented fields without requiring an undocumented `type`, and skips explicit non-tweet types. Neither integration has been live-verified. Current account permissions, actor output, exact X sort enum, cost/quota behavior, and X retweet metadata remain unknown. A PRAW-backed Reddit adapter remains a cost-reduction investigation, not an assumed free replacement. |
| E — End-to-end orchestration | IMPLEMENTED LOCALLY; LIVE-PROVIDER VALIDATION PENDING | Bounded synchronous `POST /api/pipeline/run` reuses shared source collectors, pending-analysis service, existing clustering function, and shared opportunity-summary helper. Persistent clustering outcomes support skipped/retried `needs_review` and failed records; additive pipeline state/lock tables are created without altering existing tables. Database-backed lock rejects concurrent runs. Expired leases are not reclaimed by API; offline recovery is documented below. Mocked tests cover stage success/partial failures, rate limits, retries, resumability, idempotency, summary parity, additive initialization, and request bounds. No live providers were called by pipeline tests. |
| F — Operator-facing API foundation | IMPLEMENTED LOCALLY; REAL-WORLD VALIDATION PENDING | Paginated/filterable/sortable opportunity listing, source observations and duplicate exclusions, review queue, safe explicit re-analysis, persisted run history, weekly ranking history/trends, and opt-in API key/rate limits are covered by isolated tests. Recorded independence remains distinct from verified independence (always zero). |
| G — User interface | IMPLEMENTED LOCALLY; LIVE API DEMO VALIDATION PENDING | Vite-proxied dashboard uses the existing health, opportunity, cluster-detail, source-yield, pipeline-status, run-history/detail, and run-start APIs. The dashboard has no automatic provider action; real API/database visibility must be verified locally by the operator. |
| H — Production readiness and GCP | NOT STARTED | No production deployment or GCP validation. |
| Human-labeled classification, clustering, and ranking evaluation | OUTSTANDING | No versioned human-labeled evaluation dataset or reproducible benchmark report. Unit tests establish tested behavior only, not real-world accuracy or threshold calibration. |
| Offline evaluation tooling | IMPLEMENTED LOCALLY; HUMAN-LABELED DATA PENDING | `app.evaluation` reads versioned JSONL labels/predictions and reports prefilter recall, classification, clustering, purchase-intent, independence, evidence integrity, and ranking metrics without provider calls or accuracy thresholds. It can produce deterministic blind samples of rejected text. Synthetic fixtures are smoke data only. |
| Production hardening | OUTSTANDING | No production deployment, GCP validation, multi-host lock ownership, operational monitoring, or production migration process has been implemented. |

## Safe stale pipeline-lock recovery

The lock lease is a liveness indicator, not proof that its owner stopped. The API rejects all lock takeovers, including requests with the deprecated `recover_interrupted_run=true` field. To recover a stranded lock safely:

1. Stop the API and confirm all worker processes that could run the pipeline have stopped.
2. Connect to the configured database through the operator's normal database administration method. Do not print credentials or `.env` contents.
3. Only after confirming no old worker is running, clear the singleton lock row:

   ```sql
   UPDATE pipeline_run_locks
   SET owner_token = NULL, started_at = NULL, locked_until = NULL
   WHERE id = 1;
   ```

4. Reset any interrupted analysis records before restarting:

   ```sql
   UPDATE submissions
   SET analysis_status = 'pending'
   WHERE analysis_status = 'processing';
   ```

5. Restart the API and verify `GET /api/pipeline/status` before starting a new pipeline run.

The status endpoint reports the current lock and persisted aggregate stage states only. Run history is available separately; live provider health is not inferred.

## Backend reliability follow-up

- Structured primary provenance uses `source_records`; per-source observations
  use the additive `source_observations` table. Weekly history uses additive
  snapshot and entry tables. Initialization was tested against the previous
  SQLite table layout and did not add or alter columns on existing tables.
- `create_all` creates missing additive tables; it is not a general schema
  migration tool. This roadmap adds no columns to existing tables. JSON analyses
  without the new buildability field remain readable but are not numerically
  ranked until that field is present.
- `GET /api/sources/yield` leaves malformed/incomplete analysis out of analyzed
  counts and returns a null rate when there is no usable denominator.
- Offline JSONL evaluation covers classification (including difficult cases),
  pairwise clustering, quote traceability, purchase-intent claims, independence,
  prefilter recall, rejected-record sampling, ranking agreement/correlation,
  and rating sensitivity. There are no accuracy thresholds; synthetic fixtures
  are smoke data only.
- Optional API-key checks and the bounded per-worker submission limiter have
  HTTP-level tests. Configure the key and rate limit explicitly; neither is
  enabled by default.
- The source orchestration endpoint recollects selected sources. For persisted
  records, the targeted analyze and cluster endpoints can be called by
  submission ID without recollecting. X collection is fixed at 50 records to
  respect the supplied actor minimum; confirm account eligibility and current
  run pricing before use.
- Validation: focused subsystem tests passed (184 tests); the full backend
  suite passed (188 tests, one Starlette/httpx deprecation warning) using an
  isolated temporary working directory and SQLite URL. The synthetic
  `app.evaluation` CLI completed and wrote its report and rejected sample to a
  temporary directory; these fixtures are smoke data, not quality evidence.
  `git diff --check` passed. No live source, Apify, or Gemini calls were made
  for the V1 roadmap task.
