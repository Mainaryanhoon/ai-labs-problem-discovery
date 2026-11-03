# Implementation gap report (living)

**Authority:** [`MASTER_SPECIFICATION.md`](../MASTER_SPECIFICATION.md)  
**Updated:** 2026-11-02 (commercial-first dual-lane docs + P0/P1/P2 closed)  
**Branch context:** `main` with Tatvic / leads-lane work.

**Architecture tracker:** [`DISCOVERY_OBJECTIVE_FIX.md`](DISCOVERY_OBJECTIVE_FIX.md) — **P0 (lanes & scoring), P1 (sources & weekly ops), P2 (yield & spend) closed.** This gap report no longer treats those as open product blockers.  

This file tracks **current** gaps vs the master. It is not a copy of Phase 1.  
Historical inventory: [`PHASE1_AUDIT_GAP_REPORT.md`](PHASE1_AUDIT_GAP_REPORT.md) (dated snapshot; stale test counts in §7).

**Evidence discipline:** Status labels below use verified repo inspection and cited user-reported test results. This documentation phase did **not** independently re-run the full suite unless noted.

---

## Status legend

| Label | Meaning |
| --- | --- |
| **Implemented and tested** | Code path exists; deterministic tests cover it (no live providers). |
| **Implemented, mock-only** | Code path exists; tests mock HTTP/Apify/LLM; live behavior unverified. |
| **Partial** | Some of the contract is met; named hole remains. |
| **Missing** | Required by master; not present as a compliant product path. |
| **Unverified (live)** | Needs a real provider / network / OAuth exercise. |

---

## Contract anchors (do not drift)

| Topic | Canonical rule |
| --- | --- |
| Discovery report | `data/reports/opportunities_YYYY-MM-DD.md` only (not `/workspace/reports/`) |
| Missing qualitative scores | Score **1** + `"no evidence"`; **effort same rule**; weights/gates unchanged |
| Email | **Gmail API only**; optional; explicit trigger; never SMTP |
| Model | LiteLLM primary; Gemini optional fallback |
| Pilot sources | docs, SE, HN, Reddit, exact policy RSS; X/YouTube opt-in (`ENABLE_X` / `ENABLE_YOUTUBE`); YouTube Data API v3 + 11-channel roster; dead RSS homepage fallback |
| Tests (Phase 6) | See Deterministic backend suite row after Phase 6 run |

---

## Gap table

| Area | Status | Notes |
| --- | --- | --- |
| Six-file policy load into analysis prompt | **Implemented and tested** | `load_policy_context` + gemini prompt tests. Master file itself not injected (soft gap). |
| Master summary in prompt | **Partial** | Six files include master pointers; full `MASTER_SPECIFICATION.md` not loaded. |
| LiteLLM analysis path | **Implemented, mock-only** | Client + analyze path tested with mocks; live Qwen/LiteLLM **Unverified (live)**. |
| Tatvic scoring formula v0.1 commercial-first, caps, product_ready + commercial bypass | **Implemented and tested** | Weights: commercial 35, demand 20, india 15, fit 10, readiness 10, effort 5, defensibility 5. |
| Missing/invalid qualitative → 1 + "no evidence" | **Implemented and tested** | Phase 2A in `tatvic_scoring.py`. Effort same rule. |
| `extraction_normalize` coerce malformed optional scores to 3 | **Implemented and tested** | Phase 3A: optional qualitative fields use `_optional_component_score_1_to_5` → `None` when missing/invalid (no coerce-to-3). Required urgency/frequency/evidence still use fallback coerce. Deterministic tests in `tests/test_extraction_normalize.py`. Live LLM output shape still **Unverified (live)**. |
| Product-ready API default | **Implemented and tested** | `product_ready_only=true`; fixtures updated after Phase 1 failures. |
| Default source wave UI | **Implemented and tested** | Frontend defaults match GOAL/master. |
| Collectors (docs, SE, HN, Reddit, articles, X, YouTube) | **Partial** | HN Algolia long-OR bug fixed (per-term queries). Live HN/SE/docs smoke verified in recovery; Reddit/Apify/Firecrawl still **Unverified (live)** for this pass. |
| HN Algolia zero-hit OR query | **Implemented and tested** | Long OR of quoted phrases returned 0 hits from Algolia; collector now queries SOURCES.md terms separately and merges. |
| Source yield / not_requested visibility | **Implemented and tested** | `normalize_source_yield` + discovery report distinguish not_requested / succeeded-zero / failed; funnel section added. |
| Fetch budget 60d / 3d overlap | **Partial** | Helpers + articles + **HN** (`numericFilters` + `should_fetch_new_item`). SE/docs/reddit still incomplete. |
| India-first preference in scoring | **Implemented and tested** | `india_relevance` weight 15; cap from verified text markers (`india_cap_from_text`). |
| Discovery report after every run / operator generate | **Implemented and tested** | Phase 3B: `run_pipeline` calls `write_discovery_opportunities_report` → `data/reports/opportunities_YYYY-MM-DD.md` (same-day overwrite). Independent of email/SMTP. Deterministic tests in `tests/test_discovery_report.py`. Live operator run still **Unverified (live)**. |
| GOAL-complete report columns | **Partial** | Discovery markdown includes GOAL-oriented fields + shortfall/watchlist; CEO HTML/MD is a separate thinner monthly artefact (`ceo-top6-*.md`). |
| CEO overwrite of discovery MD | **Implemented and tested** | Phase 3C: `persist_ceo_report` writes `ceo-top6-{month}.md` / `ceo-top6-latest.md` only; does not touch `opportunities_YYYY-MM-DD.md`. |
| Email via Gmail API | **Implemented and tested** | Deterministic tests + Phase 4 live send succeeded (`status=sent`, message id present). Discovery MD path unchanged. |
| SMTP | **Out of contract / removed** | No `smtplib` / SMTP settings on the product path (`config`, `.env.example`, `ceo_report`). |
| Live default-wave E2E + LiteLLM | **Verified (live)** | Phase 4 run `78b3e642-…` (historical). Phase 6 does not claim new live commercial-lead discovery. |
| Source-role demand floor | **Implemented and tested** | Phase 6: `source_roles.py` + `build_opportunity_summary` ignore model `KEEP_DEMAND` on non-demand sources (HN/docs/articles/unknown). Seed demo uses 3 demand-role items + HN capability. |
| Commercial scorer v0.2 | **Implemented, inactive** | `commercial_scoring.py` (`v0.2-commercial`) dual-returned as `commercial_score_v02`; **not** used for ranking. Activation needs Khilav/Ravi. |
| India / company commercial fields | **Partial** | `commercial_qualification` computed on summary/report; no company fabrication; India enum from stored markers only. Company-level leads remain unavailable under current sources. |
| Source-gap proposal | **Documented** | `docs/SOURCE_GAP_PROPOSAL.md` — user must edit `SOURCES.md` before any new crawl. |
| Source expansion catalogue | **Documented** | Phase 7: `docs/SOURCE_EXPANSION_CATALOG.md` — verified RSS/API probes, role matrix, Waves 1–4, pilot. **No collectors added.** |
| Deterministic backend suite | **Implemented and tested** | Phase 6 re-run: **254 passed**, 1 Starlette/httpx warning (`python -m pytest -q`). |
| Frontend suite | **Implemented and tested** *(earlier)* | Phase 1 recorded 5/5; not re-run here. |

---

## Priority follow-ups (implementation phases — not this doc pass)

1. **Fetch budget:** Extend 60d/3d gate beyond HN/articles to SE, docs, Reddit.  
2. **Prompt:** Optional short master excerpt/pointer beyond the six files.  
3. **Live verification:** Full default-wave run (incl. Reddit Apify) + LiteLLM analysis + report rewrite; record honest yield. Live Gmail send when desired.  

Do **not** add X/YouTube to the **default** pipeline wave (keep `ENABLE_*` flags). Do not reactivate inactive `v0.2-commercial` as the sort key. Lane 1 commercial bypass of Tatvic-offering **keyword** gates is intentional.

---

## Product decisions still needed (or newly locked)

| Topic | Status |
| --- | --- |
| Gmail API only (no SMTP) | **Locked** in MASTER §7.1 |
| Gmail OAuth client ownership / which Google Cloud project | **Open** (ops) |
| Whether CEO email is ever required for “done” | **Locked:** optional; local report required |
| Normalizer coerce-to-3 vs strict reject | **Resolved (Phase 3A)** for optional qualitative components; required ratings still use coerce-with-default |
| Inject full master vs short summary into prompt | **Open** (token budget) |
| Activate `v0.2-commercial` for ranking | **Won't do as sort key** — v0.1 commercial-first is active; v0.2 remains comparison-only; weekly leads use v0.3-leads |
| Expand `SOURCES.md` for company/India/buying triggers | **Blocked** on manual review of `docs/SOURCE_EXPANSION_CATALOG.md` (§9 + §11) |

---

## What this file is not

- Not permission to silently change default sources or reactivate v0.2 as ranking.  
- Not proof of live provider readiness.  
- Not a substitute for `MASTER_SPECIFICATION.md`.  
