# Phase 1 — Python application audit & gap report

> **HISTORICAL SNAPSHOT.** Captured 2026-10-28 during Phase 1.  
> Live gap tracking: [`IMPLEMENTATION_GAP_REPORT.md`](IMPLEMENTATION_GAP_REPORT.md).  
> Product SoT: [`../MASTER_SPECIFICATION.md`](../MASTER_SPECIFICATION.md).  
> Do not treat §7 test counts below as current status without checking the living gap report.

**Date:** 2026-10-28T11:41:48Z  
**Scope:** Read-only audit (no application code changes).  
**Mapped to:** [`MASTER_SPECIFICATION.md`](../MASTER_SPECIFICATION.md)  
**Baseline tests (historical):** see §7  

This report describes the **as-built** flow at audit time, inventories call sites, and maps gaps to the master. Keep findings identifiable as historical. Later phases reconciled some items (fixture tests; missing-score defaults in `tatvic_scoring`); others remain open in the living gap report.

---

## 1. End-to-end flow (as implemented)

```text
Operator UI / POST /api/pipeline/run
  → collectors (per selected source)
      → Submission (+ SourceRecord / SourceObservation / provider metadata)
  → analyze_pending_batch
      → analyze_submission (LiteLLM primary | Gemini fallback)
          → schema normalize (extraction_normalize) + quote sanitize
  → cluster_submission (exact dedupe → semantic match → new cluster / needs_review)
  → build_opportunity_summary
      → tatvic_scoring.score_tatvic_opportunity (+ legacy ranking helpers)
  → API: /api/opportunities (default product_ready_only=true)
  → Frontend Discover / Opportunities / Rankings
  → Optional: weekly/monthly snapshots + POST /api/reports/monthly/ceo
      → data/reports/ceo-top6-*.{html,txt} + opportunities_YYYY-MM-DD.md
```

| Stage | Primary modules | Status |
| --- | --- | --- |
| Collection | `ingestion.py`, `social_ingestion.py`, `docs_ingestion.py`, `article_ingestion.py`, `youtube_ingestion.py`, orchestrated by `services/pipeline.py` | **Works locally** with mocked HTTP/Apify in tests. Live providers **unverified**. |
| Hygiene / cheap filters | `docs_filter_helpers.py` (12-word gate, fetch budget, Reddit topic allowlist), HN `PROBLEM_SIGNALS` | **Works**; Reddit topic gate is stricter than some older fixtures expect (see §7). |
| Normalization | `ingestion.clean_text` / `normalize`, `clustering.canonicalize_url` / `normalize_text`, `extraction_normalize` for model JSON | **Works** (unit coverage). |
| Analysis | `services/gemini.py` → `services/llm.py` | **Implemented**; deterministic tests mock analyzer. Live LiteLLM/Gemini **unverified** in this audit. |
| Dedup / clustering | `clustering.py`, `provenance.py` | **Works** in deterministic tests; embedding path falls back to local hash when embedding model unset. |
| Ranking | `tatvic_scoring.py` (canonical Tatvic score), `ranking.py` (legacy eligibility / buildability helpers still used inside `build_opportunity_summary`) | **Works** for product-ready path; dual-layer is intentional legacy, not a second product. |
| Database | SQLAlchemy models in `models.py`, `db.initialize_database` | **Works**; additive tables via `create_all`, not a migration system. |
| Report | `services/ceo_report.py` | **Implemented** for monthly CEO path; **no** `data/reports/` present on disk at audit time. GOAL-style MD is written when CEO report runs, not automatically after every pipeline run. |
| Dashboard | `frontend/` → proxied FastAPI | **Works** with mocked tests (5/5). Live API demo is operator-dependent. |

---

## 2. Inventory

### 2.1 LLM calls

| Call site | Function | Provider | Notes |
| --- | --- | --- | --- |
| `app/services/llm.py` | `chat_completion_json` | LiteLLM OpenAI-compatible `POST …/chat/completions` | Primary path when `LITELLM_API_KEY` + base URL set |
| `app/services/gemini.py` | `analyze_submission` | Calls `chat_completion_json` first | Loads six Tatvic policy files into prompt |
| `app/services/gemini.py` | `_analyze_with_gemini` | Google Gen AI SDK | Only if LiteLLM not configured and `GEMINI_API_KEY` set |
| `app/services/analysis.py` | `analyze_pending_batch` | Injects `analyzer` (default `analyze_submission`) | Pipeline + `/api/submissions/analyze-pending` |
| `app/main.py` | submission analyze routes | Same analyzer | Per-id and batch |

**Not found:** separate “scoring LLM” call. Component 1–5 scores are expected from analysis JSON; demand/final arithmetic is code in `tatvic_scoring.py`.

### 2.2 Embedding calls

| Call site | Function | Behavior |
| --- | --- | --- |
| `app/services/llm.py` | `get_embeddings` | LiteLLM `POST …/embeddings` **only if** `LITELLM_EMBEDDING_MODEL` set; else deterministic local 64-d hash vectors |
| `app/clustering.py` | `get_embeddings` | Wraps LLM embeddings + validation; used in semantic cluster match |

### 2.3 Source collectors

| Source key | Module | Role vs master §5 | Live verification |
| --- | --- | --- | --- |
| `official_docs` | `docs_ingestion.collect_official_docs` | Default wave — capability | Unverified live |
| `stack_exchange` | `ingestion.collect_stack_exchange` | Default wave — demand | Unverified live (mocked tests) |
| `hacker_news` | `ingestion.collect_hacker_news` | Default wave — corroboration | Unverified live (mocked tests) |
| `reddit` | `social_ingestion.collect_reddit` | Default wave — demand pilot | Unverified live (Apify) |
| `articles` | `article_ingestion.collect_articles` | Capability context (allowlisted RSS → Firecrawl → Playwright) | Unverified live |
| `x` | `social_ingestion.collect_x` | **Opt-in only** | Unverified live |
| `youtube` | `youtube_ingestion.collect_youtube` | **Opt-in only** | Unverified live |

UI default selection matches master default wave (`official_docs`, `stack_exchange`, `hacker_news`, `reddit`) — X/YouTube unchecked by default.

Standalone `/api/ingestion/*` routes are **not** registered in `main.py`; collection is via `POST /api/pipeline/run` (and internal `ingest_*` helpers used by tests).

### 2.4 API endpoints (FastAPI)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | API pointer (no UI) |
| GET | `/health` | Health / version |
| POST | `/api/submissions` | Manual submission |
| GET | `/api/submissions` | List |
| GET | `/api/submissions/{id}` | Detail |
| POST | `/api/submissions/{id}/analyze` | Analyze one |
| POST | `/api/submissions/analyze-pending` | Batch analyze |
| POST | `/api/submissions/{id}/cluster` | Cluster one |
| POST | `/api/clusters` | Create cluster |
| GET | `/api/clusters` | List clusters |
| GET | `/api/clusters/{id}` | Cluster detail |
| GET | `/api/clusters/{id}/opportunity` | Opportunity summary |
| POST | `/api/clusters/{id}/evidence` | Attach evidence |
| GET | `/api/opportunities` | Ranked list (`product_ready_only` **default true**) |
| GET | `/api/policy/tatvic` | Policy load status / weight version |
| GET | `/api/review-queue` | REVIEW / needs_review style queue |
| GET | `/api/sources/yield` | Source yield stats |
| POST | `/api/pipeline/run` | Full orchestrated run |
| GET | `/api/pipeline/status` | Lock / stage status |
| GET | `/api/pipeline/runs` | Run history |
| GET | `/api/pipeline/runs/{id}` | Run detail |
| POST/GET | `/api/rankings/weekly/snapshots…` | Weekly snapshots + trend |
| POST/GET | `/api/rankings/monthly/snapshots…` | Monthly snapshots |
| POST | `/api/reports/monthly/ceo` | Persist CEO Top-K + opportunities MD (+ optional SMTP) |

Docs protection middleware may gate `/docs` depending on `SERVICE_API_KEY`.

### 2.5 Database models

| Model | Table | Role |
| --- | --- | --- |
| `Submission` | `submissions` | Raw text + analysis fields |
| `ProblemCluster` | `problem_clusters` | Opportunity cluster |
| `ClusterEvidence` | `cluster_evidence` | Membership / independence flags |
| `SourceRecord` | `source_records` | Per-submission native id / timestamp origin |
| `SourceObservation` | `source_observations` | Per-source URL/native observations (dedupe) |
| `SubmissionProviderMetadata` | `submission_provider_metadata` | Provider payload extras |
| `PipelineRun` / `PipelineRunSubmission` / `PipelineRunLock` / `PipelineSubmissionState` | pipeline_* | Run orchestration |
| `WeeklyRankingSnapshot` / `WeeklyRankingEntry` | weekly_* | Weekly history |
| `MonthlyRankingSnapshot` / `MonthlyRankingEntry` | monthly_* | Monthly / CEO Top-K |

SQLite default: `sqlite:///./data/problem_discovery.db`.

---

## 3. What works (evidence: tests / code paths)

- Pipeline orchestration with lock, stage failures, and run-scoped submissions (`tests/test_pipeline.py` majority).
- LiteLLM client + Gemini fallback structure; schema validation and quote sanitization (`tests/test_gemini_service.py`).
- Extraction normalizer for Qwen/schema drift (`tests/test_extraction_normalize.py`).
- Tatvic scoring formula v0.1, product_ready gates, keep/drop example alignment (`tests/test_tatvic_scoring.py`, `tests/test_keep_drop_examples.py`).
- Clustering exact-text / URL dedupe and conservative semantic path (`tests/test_clustering.py`).
- HN / SE collectors under mocks; article feed parsing tests; CEO report rendering tests.
- Frontend Discover spotlight + Sources & runs default wave (`frontend` 5/5).
- Policy files load from disk (`policy_files_loaded()` returns all six).

---

## 4. Incomplete / weak relative to master

| Area | Finding |
| --- | --- |
| Khilav report after every discovery run | `opportunities_YYYY-MM-DD.md` is written inside **CEO monthly** persist path, not automatically at end of `run_pipeline`. `data/reports/` absent at audit time. |
| Master in model prompt | Prompt loads six OpenClaw files only; **does not** inject `MASTER_SPECIFICATION.md` (master says supporting docs must obey it — prompt gap). |
| India-first | No geo/vertical preference logic in scoring beyond optional vertical field in schema. Framing is documentation-only today. |
| Watchlist | Emerging/non–product-ready clusters exist in DB but Discover defaults to product_ready only; no dedicated watchlist artefact in report format. |
| Live acceptance path | Default wave + LiteLLM end-to-end not proven in this audit. |
| Human-labeled quality | Explicitly out of deadline scope; still a product risk. |
| Dual scoring heritage | `ranking.score_opportunity` still influences eligibility/buildability paths; display score for opportunities is Tatvic score. Risk of operator confusion, not currently a second ranking product. |

---

## 5. Mocked / unverified (do not claim “live done”)

- Apify Reddit / X / YouTube actors, account eligibility, cost, output schema.
- Stack Exchange / HN live rate limits and auth needs.
- Firecrawl / Playwright article page fetch in production network conditions.
- LiteLLM endpoint reachability and Qwen schema stability on real posts.
- Gemini fallback quota behavior.
- SMTP CEO email delivery.
- Seeded demo cluster (`scripts/seed_demo_opportunity.py`) is a **UI proof** aid, not live demand proof.

---

## 6. Gap matrix vs MASTER_SPECIFICATION.md

| Master requirement | Code / ops status | Gap? |
| --- | --- | --- |
| §1 Tatvic opportunities for Khilav | Policy prompts + Tatvic scoring + UI branding | Partial — report automation incomplete |
| §2 India-first | Docs only | **Gap** — no scoring/report preference wired |
| §3 Sections incl. Agentic AI | Prompt + RUBRIC + scoring sections | OK in schema; live quality unverified |
| §4 Evidence floors / product_ready | `is_product_ready`, caps, demand anchors | **OK** (code); tests lagged API default (below) |
| §5 Default sources; X/YouTube opt-in | UI defaults + pipeline source keys | **OK** |
| §5 Fetch budget / no re-fetch | Helpers + provenance known ids/urls | Implemented; live unverified |
| §6 LiteLLM primary | `llm_configured()` first in analyze | **OK** structurally; live unverified |
| §6 Embeddings | Optional remote; else local hash | OK for offline; semantic quality limited without embedding model |
| §7 5–10 / ≤6 CEO / UI one | UI spotlight + `MONTHLY_TOP_K`; opportunities list can return many product_ready | Soft gap — no hard “max 10” clamp on discovery report generation |
| §8.A Runnable local | Supported by README / scripts | Operator-dependent |
| §8.B Honest E2E default wave | Code path exists | **Gap** — not verified live this audit |
| §8.C Report under `data/reports/` readable alone | CEO path can write MD | **Gap** — directory missing; not tied to every pipeline run; MD thinner than GOAL table |
| §8.D Policy fidelity | Six files in prompt; master not in prompt | Soft gap |
| §8.E ≥1 product_ready + tests green | Seed script can create one; **tests not fully green** | **Gap** — 3 failing tests |
| Non-goals (GCP, TAM, India-only crawl) | Not implemented | OK |

---

## 7. Test baseline (recorded)

### Backend

```text
Command: python -m pytest -q
Result:  3 failed, 214 passed, 1 warning
Time:    ~5.3–5.8s
Warning: Starlette TestClient / httpx deprecation
When:    2026-10-28T11:41:48Z
```

| Failure | Likely cause (audit hypothesis) | Relation to master |
| --- | --- | --- |
| `test_opportunities_sort_scores_before_pagination_and_apply_filters` | `GET /api/opportunities` now defaults `product_ready_only=true`; fixture clusters are scored but not product_ready → `total == 0` | Stricter listing matches master precision; **tests outdated**, not necessarily wrong product behavior |
| `test_weekly_ranking_snapshots_are_idempotent_and_trends_are_traceable` | Weekly snapshot likely empty for same product_ready / eligibility filter interaction | Same family |
| `test_all_ingestion_adapters_persist_native_identity_and_timestamp_origin` | Reddit `_save_items` returns no ids: `r/SaaS` + text fails `reddit_topic_allowed` keyword gate | SOURCES.md keyword rule for broad subreddits; **fixture outdated** |

### Frontend

```text
Command: npm test -- --run (frontend/)
Result:  1 file passed, 5 tests passed
```

**Acceptance §8.E item 15 at Phase 1 time:** FALSE (three failures above).

### Later status note (not an independent re-run of Phase 1)

After Phase 1, fixture reconciliation and Phase 2A missing-score work landed in the open working tree. The **latest user-reported** full backend result cited for documentation reconciliation is **223 passed, 1 Starlette/httpx warning**. This Phase 1 file does **not** claim that number was produced by re-running pytest during the documentation phase. See `IMPLEMENTATION_GAP_REPORT.md` for living status.

---

## 8. Recommendations for later phases (recorded at Phase 1; historical)

1. **Do not rewrite** collectors, clustering, or LiteLLM client without a failing live or test case.  
2. Reconcile the **three failing tests** with `product_ready_only` and Reddit keyword gates (test/fixture alignment).  
3. Wire a **pipeline-complete → `opportunities_YYYY-MM-DD.md`** path (or explicit operator “Generate discovery report”) to close §8.C.  
4. Optionally include a short **master summary** (or pointer) in the analyst prompt — avoid dumping the entire master if token-limited.  
5. Prove one **live** default-wave run against LiteLLM before claiming deadline done.  
6. Keep X/YouTube opt-in; do not expand default sources to chase volume.

---

## 9. Module map (quick reference)

| Concern | Prefer these modules |
| --- | --- |
| Orchestration | `app/services/pipeline.py` |
| Analysis I/O | `app/services/gemini.py`, `app/services/llm.py`, `app/extraction_normalize.py` |
| Tatvic score / product_ready | `app/tatvic_scoring.py` |
| Opportunity API assembly | `app/main.py` → `build_opportunity_summary` |
| Legacy score helpers | `app/ranking.py` |
| Provenance | `app/provenance.py` |
| Reports | `app/services/ceo_report.py` |
| Policy on disk | `docs/tatvic/*` + `MASTER_SPECIFICATION.md` |

---

*End of Phase 1 audit. Next engineering work should cite gaps above before changing functioning modules.*
