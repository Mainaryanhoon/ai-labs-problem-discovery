# Discovery objective fix tracker

**Status:** CLOSED — P0 (lanes & scoring), P1 (sources & weekly ops), and P2 (yield & spend) architectural blockers are complete.  
**Opened:** 2026-11-02  
**Closed:** 2026-11-02  
**Owners:** Khilav Joshi / Ravi Pathak (scoring and eligibility); engineering (implementation)  
**Related:** [`MASTER_SPECIFICATION.md`](../MASTER_SPECIFICATION.md), [`docs/IMPLEMENTATION_GAP_REPORT.md`](IMPLEMENTATION_GAP_REPORT.md), leads lane (`app/leads/`, `scripts/weekly_run.py`)

Do not treat this file as a second product spec. Canonical intent is MASTER + `docs/tatvic/*`. Live provider calibration and human ranking review remain operational work, not a re-open of P0–P2.

---

## 0. Target architecture (what we built)

**Manager objective:**

> Find new paid problems first, determine Tatvic delivery second.

**Lane split:**

| Lane | Purpose | Must not |
| --- | --- | --- |
| **Lane 1 — Market leads / ideas** | New paid problems, market intelligence, founder/investor/budget-holder signals | Require existing Tatvic section + offering angle to exist |
| **Lane 2 — Tatvic delivery** | After a lead exists: fit, build vs buy, service vs product, first customer | Gate the lead out of the weekly report for failing Tatvic-offering **keyword** gates |

Tatvic fit is a **score and a note**, not an eligibility kill-switch for discovery.

**Commercial bypass:** `commercial_signal >= 4` or a C-suite / budget-holder buyer skips `is_product_ready()` offering-keyword gates.

**Weekly command:** `python scripts/weekly_run.py` (collect → extract → report). `python -m app.leads` does **not** collect.

---

## 1. How to use this file

Historical. P0–P2 checkboxes below are `[x]`. Do not re-open them to chase volume. Remaining holes belong in [`IMPLEMENTATION_GAP_REPORT.md`](IMPLEMENTATION_GAP_REPORT.md) (live providers, OAuth ops).

---

## 2. Inventory as of 2026-11-02 (closed)

| Reviewer claim | Repo after close | Tracker |
| --- | --- | --- |
| X policy not updated | `SOURCES.md` §8: opt-in `ENABLE_X`, limit 50, Indian startup/D2C queries | P1 closed |
| YouTube / Reddit / source policy | Data API v3 primary + Apify fallback; 11-channel roster in SOURCES + `.env.example`. Reddit is default **pipeline**; weekly_run does not collect Reddit | P1 closed |
| `market` / `idea` roles | Wired into qualification + `N1_IDEA_CONTEXT` | P0 closed |
| `N1_IDEA_CONTEXT` | In schemas, classifier prompt, RUBRIC, scoring keep-codes | P0 closed |
| Dead RSS / 12-feed cap | `MAX_ARTICLE_FEEDS = 20`; Entrackr/FE **not** live RSS; homepage Playwright fallback | P1 closed |
| Scoring tables | Active v0.1 commercial-first matches code; v0.2 inactive; v0.3-leads for weekly leads | P0 closed |
| Source rankings from yield | `SOURCE_RANKING.md` points at `source_yield` / `feed_statuses`; judgment rows labeled as such | P2 closed |
| `is_product_ready()` blocks general ideas | Commercial bypass + market-lead floor | P0 closed |
| Report wording | Weekly “Weekly Business Leads”; no “validated demand” | P0 closed |
| Weekly workflow | `python scripts/weekly_run.py` documented in MASTER/GOAL/README | P1 closed |
| Practitioner-only KEEP | Practitioner valid, not exclusive; `N1_IDEA_CONTEXT` | P0 closed |

Preserve: provenance, quote substring checks, source-role demand floor, cluster dedupe, LiteLLM-first, Gmail-only email, X/YouTube **off unless flags**.

---

## 3. P0 — discovery can surface paid problems

### 3.1 One objective, one taxonomy

- [x] Rewrite [`MASTER_SPECIFICATION.md`](../MASTER_SPECIFICATION.md) §1: paid problems first, Tatvic delivery second. Practitioner signals are not the only collectable evidence.
- [x] Five-section list is canonical (Analytics, Cloud and Data Engineering, Data Science and AI/ML, Agentic AI, CRO). Legacy “Data Science and AI” alias remains in code only.
- [x] `docs/tatvic/*`, GOAL, SOUL, AGENTS, this tracker, and code describe the same lanes, sources, and v0.1 commercial-first table.
- [x] `PROJECT_CHECKLIST.md` and `IMPLEMENTATION_GAP_REPORT.md` point at this closed tracker (stop advertising 223/254 as current; 345 passed 2026-11-02).

### 3.2 Market / idea lane

- [x] **N1_IDEA_CONTEXT** in RUBRIC + filter/schema; market/idea items not dropped as DROP_NOT_PRACTITIONER / DROP_NO_PROBLEM / DROP_OUT_OF_SCOPE_TOPIC.
- [x] `market` / `idea` roles in qualification (`is_market_lead_role`).
- [x] KEEP_DEMAND + offering angle are Lane 2; Lane 1 can appear without them.
- [x] Demand-floor rules still apply for the product-ready partition.

### 3.3 Scoring

- [x] Active v0.1: commercial_signal 35, demand_strength 20, india_relevance 15, section_client_fit 10, capability_readiness 10, effort_to_first_version 5, defensibility 5.
- [x] v0.2-commercial remains inactive comparison-only; v0.3-leads ranks weekly leads.
- [x] RUBRIC / SCORING_RULES match code.
- [x] india_relevance capped by verified text markers.

### 3.4 Output contract

- [x] Discovery/leads reports do not say “Validated …”.
- [x] Three-line exec summary on weekly leads artefact.
- [x] why now / who pays / first-customer reach / service vs product fields.
- [x] Idea log + watchlist in leads lane.

---

## 4. P1 — sources, collection, weekly ops, feedback

### 4.1 Policy and execution the same

- [x] X excluded by **default**; `ENABLE_X` collects Indian startup/D2C queries at limit 50 (not a default demand source).
- [x] YouTube: Data API v3 primary, Apify fallback, 11-channel roster in SOURCES + `.env.example`.
- [x] Reddit: default pipeline wave; **unused by weekly leads**.
- [x] Mint Startups: still absent until `check_feeds.py` is OK (not an architectural blocker; do not add a dead feed).
- [x] Feed fetch: `follow_redirects=True` with allowlisted final URL; homepage scrape fallback on HTTP RSS failure.

### 4.2 Collection limits

- [x] `MAX_ARTICLE_FEEDS = 20`; per-feed ceil cap remains.
- [x] Weekly leads `ignore_cursor=True`; stored URL/native-id still dedupe (including Google Developer Forums).
- [x] Pipeline request bounds remain operator-set (default 50 on articles/analysis/cluster in current schemas).

### 4.3 Weekly execution

- [x] Canonical weekly command: `python scripts/weekly_run.py`.
- [x] Bounded run: `LEADS_COLLECT_LIMIT` + `LEADS_MAX_LLM_CALLS` (including 0).
- [x] Cron example in script header only; not activated in-repo; email not sent by this script.
- [x] Bounded/live runs write `data/reports/leads_YYYY-MM-DD.md` and `funnel_*.json`.

### 4.4 Accept / reject / learn

- [x] Leads CLI `accept`/`reject` with reason; host adjustment on future ranking.

---

## 5. P2 — yield, report polish, spend honesty

- [x] `SOURCE_RANKING.md` instructs ranking from measured `source_yield` / `feed_statuses`; static scores labeled judgment until more runs.
- [x] First-customer strategy is a named route field (not a job title alone).
- [x] Idea lifecycle in leads `IdeaLog` (suppress / resurface on score delta).
- [x] Spend knobs documented (LiteLLM/Gemini, Firecrawl, Apify, YouTube Data API). Default first run: small or 0 `LEADS_MAX_LLM_CALLS`.

---

## 6. Preflight audit defects — resolved

| Feed | Action |
| --- | --- |
| ET / YourStory / Inc42 / Mint / TechCrunch / Medianama / AIM | Remain live RSS |
| **entrackr.com/feed** | Removed from **active RSS** list; homepage fallback loop only |
| **financialexpress.com/feed/** | Same |
| Google marketing / AI / forums RSS | Inside `MAX_ARTICLE_FEEDS = 20` |

- [x] `scripts/check_feeds.py` remains a **proposal probe** (candidate list), not a clone of `article_feeds()`.
- [x] Collector allowlists the final HTTPS host after redirects.
- [x] `python -m app.leads` ≠ `python scripts/weekly_run.py` (README).
- [x] Weekly run collects X/YouTube **only** when `ENABLE_X` / `ENABLE_YOUTUBE` are set.

---

## 7. Why empty reports happen (historical)

Empty **opportunities_*.md** while **leads_*.md** is populated is expected: two artefacts, two lanes. Do not “fix” Lane 1 emptiness by adding sources if commercial bypass and N1_IDEA_CONTEXT are already on.

---

## 8. Anti-repeat rules (still in force)

1. A policy file must not forbid a source that a default code path collects as demand.
2. A policy file must describe the collection method the code uses (YouTube Data API v3 primary).
3. `.env.example` channel/feed lists must agree with `SOURCES.md`.
4. Scoring weights in `RUBRIC.md` must match the ranking function.
5. `market` / `idea` must be able to produce a report row (`N1_IDEA_CONTEXT`).
6. The weekly command in docs must be `python scripts/weekly_run.py`.
7. The report must not call opportunities “validated”.
8. `is_product_ready()` keyword gates must not hide a commercial-bypass / budget-holder lead.
9. Dead RSS URLs must not sit on the **active** RSS list (homepage fallback only).
10. Checker OK is not proof the collector can fetch (redirect / allowlist).

---

## 9. Verification

```bash
.venv/bin/python -m pytest -q tests/test_leads_lane.py
.venv/bin/python -m pytest -q
git diff --check
```

Feed-only (no LLM, no weekly pipeline):

```bash
.venv/bin/python scripts/check_feeds.py
```

Bounded weekly:

```bash
LEADS_COLLECT_LIMIT=10 LEADS_MAX_LLM_CALLS=0 python scripts/weekly_run.py
```

With opt-in sources:

```bash
ENABLE_X=true ENABLE_YOUTUBE=true python scripts/weekly_run.py
```

---

## 10. Close-out

- [x] All P0 checkboxes `[x]`
- [x] All P1 checkboxes `[x]`
- [x] All P2 checkboxes `[x]`
- [x] §6 dead feeds resolved (homepage fallback; not active RSS)
- [x] Next review uses MASTER + this closed file; GOAL/SOURCES/RUBRIC/code describe the same commercial-first dual-lane system

**Closed date:** 2026-11-02
