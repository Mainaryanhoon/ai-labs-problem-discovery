# Problem Discovery — Handoff

State: 2026-10-20. Branch inspected: `feat/problem-discovery-foundation`.

## Architecture (10 lines)
1. `app.main` is the FastAPI application and exposes health, ingestion, pipeline, opportunity, review, and ranking APIs.
2. SQLAlchemy persists submissions, clusters, evidence, source provenance, and pipeline state in the configured database.
3. Hacker News and Stack Exchange collectors are in `app.ingestion`; Reddit and X adapters are in `app.social_ingestion`.
4. `app.provenance` records source-native identifiers, timestamps, and observations while avoiding duplicate analysis records.
5. `app.services.pipeline` coordinates collection, analysis, clustering, and opportunity summaries with persisted run IDs and run-scoped submissions.
6. `app.services.analysis` selects analysis work; `app.services.gemini` validates structured model output.
7. `app.clustering` embeds submissions, checks candidate relevance, and preserves human review for borderline matches.
8. `app.ranking` scores eligible opportunities; `app.services.rankings` persists weekly snapshots and trends.
9. `app.evaluation` runs offline JSONL classification, prefilter, clustering, evidence, and ranking measurements.
10. `frontend/` is a React/TypeScript/Vite UI using existing FastAPI APIs; collection requires an explicit operator action.

## What works

- Backend tests: **188 passed, one deprecation warning — executed** in the latest full-suite verification. An earlier pre-roadmap checkpoint had 178 passing tests; use 188 as the later result.
- Frontend tests: **5 passed — executed** with mocked API calls. TypeScript/production build passed and `npm audit` reported zero vulnerabilities — executed.
- The frontend preview used mocked API responses; it did not start collection or contact providers.
- Additive database initialization was tested against a temporary prior-schema SQLite database; historical rows were preserved.
- Dashboard read surfaces use real API contracts and the UI does not auto-start a pipeline run.

## Unverified

- Live Apify/Reddit/X/Hacker News/Stack Exchange and Gemini behavior, account access, pricing, and provider output contracts.
- A real-data dashboard session against the intended Ubuntu backend/database.
- A standalone fixture-demo command covering fixture collection through API retrieval; mocked end-to-end pipeline tests exist, but no such standalone demo was run.
- The currently running backend version and its live routes. A listener occupied port 8000 during inspection; it was not queried or restarted.
- Human-labeled classification, clustering, prefilter recall, and ranking quality. Synthetic fixtures do not establish population accuracy.

## Known issues and cautions

- A prior runtime report observed `GET /api/pipeline/runs` returning 404 from a stale server, although current source registers the route. Restart the intended backend and check `/openapi.json` before treating this as a source regression.
- Keyword-prefilter recall is an open problem. The synthetic benchmark has no `prefilter_kept` decisions; its conservative missing-decision treatment cannot measure population recall.
- Reddit actor `maxItems` is reported to apply per `startUrl`; the current operator documentation describes it as an overall cap. Verify the actor contract and reconcile documentation before relying on per-community counts.
- X collection requires the backend's fixed minimum of 50 tweets per query. Do not assume free-plan API access; account eligibility and current pricing remain unverified.
- The browser UI does not embed `SERVICE_API_KEY`. If API-key protection is enabled, use an appropriate trusted local proxy rather than putting a secret in the browser bundle.

## Open decisions

- Choose an Ubuntu release and Python installation: requirements do not pin Python; source syntax requires Python 3.10 or later.
- Confirm current Reddit/X actor contracts, account access, and spend limits before any live run.
- Decide whether a fixture demo should be a CLI command or a documented test invocation; keep its database temporary and its providers mocked.
- Define a small human-labeled evaluation set before making accuracy or prefilter-recall claims.

## Next milestone

Deliver one reproducible fixture-based demo on Ubuntu: use a temporary database and deterministic fixture collectors, mock analysis and embeddings, execute the run-scoped pipeline, then retrieve the run and opportunity through the API/UI. Verify stale-record isolation, duplicates/provenance, partial failures, no provider calls, and an honest empty state. Do not expand architecture before this demo is repeatable.
