# GOAL.md — What we are building and what done means

> **Canonical product intent:** repository root [`MASTER_SPECIFICATION.md`](../../MASTER_SPECIFICATION.md).  
> This file is the operational goal + report format for the Tatvic brain. If anything conflicts, the master wins.

## Goal

**Find new paid problems first, determine Tatvic delivery second.**

Find evidence-backed **business leads** for **Tatvic Labs** (India-first commercial context). Each lead is a problem a budget holder may pay to solve. Practitioner community posts are valid evidence; they are **not** the only collectable evidence. Market articles, founder/operator statements, company triggers, opt-in X, and the YouTube channel pilot also count when the quote is real.

The final reader is **Khilav Joshi**.

### Dual lanes

| Lane | Purpose |
|---|---|
| **Lane 1 — Market leads / ideas** | Budget-holder signals and market intelligence. Weekly command: `python scripts/weekly_run.py` → `data/reports/leads_YYYY-MM-DD.md`. |
| **Lane 2 — Tatvic delivery** | After a lead exists: section fit, build vs buy, service vs product, first customer. `is_product_ready()` lives here. Commercial signal ≥4 or a C-suite/budget-holder buyer bypasses Tatvic-offering **keyword** gates. |

- Target: at least 5 and at most 10 ranked discovery rows when evidence exists; never pad. Weekly leads may be fewer; shortfalls go on the watchlist.
- Opportunity/lead results do not expire. A kept problem stays one cluster and is not listed again as a new opportunity unless score moves enough (leads `IdeaLog`).
- Collection is incremental so the same item is not fetched twice and provider limits are not exhausted. See RUBRIC.md section 2. Weekly leads may set `ignore_cursor=True`; stored URL/native-id still prevent duplicates.
- Build timeline: the whole system is built and working by **2026-10-30** (acceptance in MASTER_SPECIFICATION §8).
- Monthly CEO shortlist may include at most **6** of the Lane 2 / Tatvic-ready set (stricter gate in TATVIC_READY.md / MONEY_AND_OFFERINGS.md). That does not hide Lane 1 leads from the weekly report.
- Optional email of the CEO shortlist uses **Gmail API only** (MASTER_SPECIFICATION §7.1). SMTP is out of contract. Email is never required for “done”; local `data/reports/` files are.

## Scope
- Sections and rules: RUBRIC.md
- Context and examples of a good **delivery** pattern: ASSETS.md (Lane 2)
- India-first buyer framing: MASTER_SPECIFICATION §2 (global English sources remain valid evidence)
- Sources: official docs (capability), Stack Exchange and Reddit (demand), Hacker News (capability corroboration), allowlisted RSS (market / idea / company_trigger / capability per URL).
- Opt-in: X (`ENABLE_X`) and YouTube (`ENABLE_YOUTUBE`) for Indian startup and D2C budget-holder pain. Not required for a default pipeline run.
- `python -m app.leads` does not collect; it extracts/reports/reviews stored items.

## Phases
1. Collect: ingest raw items from configured sources
2. Classify: apply the gates in RUBRIC.md (reason code + one sentence per item), including `N1_IDEA_CONTEXT` for market/idea
3. Cluster: group items about the same problem; code computes counts
4. Match: Lane 2 — join to capability items and ASSETS.md; choose one offering angle
5. Score: commercial-first formula in RUBRIC.md (`v0.1`); weekly leads `v0.3-leads`
6. Report: write the weekly leads report and/or discovery report for human review

## Definition of done
The system is done when acceptance criteria in MASTER_SPECIFICATION §8 are met: `python scripts/weekly_run.py` produces a report under `data/reports/` that Khilav can read without opening any other file, every claim traces back to stored submissions and analysis JSON, default pipeline wave does not require X/YouTube, and analysis is local-LiteLLM-first.

## Report format

Weekly leads: `data/reports/leads_YYYY-MM-DD.md` (three-line exec summary, who pays, why now, first-customer route, service vs product).  
Discovery: `data/reports/opportunities_YYYY-MM-DD.md` (the run date).  
No alternate product path (e.g. `/workspace/reports/`).

Header:
- Run date, collection window, weight version from RUBRIC.md
- Items collected per source
- Items per reason code
- Number of items waiting in the review queue

Section 1: Top opportunities / leads (ranked table, one row per item)
Columns: Rank and name, Section and vertical, Problem in one sentence, Who pays, Evidence level, Counts from code, Score out of 100, Why now, First-customer route, Service vs product, Offering angle (Lane 2), Smallest first version, Why it might fail.
