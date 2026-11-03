# Tatvic-ready brain — what may be ranked

> Authority: [`MASTER_SPECIFICATION.md`](../../MASTER_SPECIFICATION.md) §§3–7.  
> This document is the **Lane 2 extra ranking gate** for CEO / commercial lists. A cluster is **Tatvic-ready** only when every gate below is true. If any gate fails, it may stay on a watchlist but must not enter Top 6 for CEO email. **Lane 1 weekly leads** (`python scripts/weekly_run.py`) are not required to pass these gates; commercial signal ≥4 or a budget-holder buyer bypasses Tatvic-offering keyword filters on `is_product_ready()`.

## Gate A — Money path

Tatvic can charge for at least one of:

1. **Service delivery** — implementation, audit, migration, optimisation.
2. **Managed agent / productised offering** — extends BQ agent, CRO Excalibur,
   measurement ops, forecasting/RCA agents, or a new section agent.
3. **Cheaper delivery** — a native capability or automation that cuts hours or
   cost on work Tatvic already sells.

No money path → not ranked for CEO report.

## Gate B — Domain fit (existing sections only)

Primary section must be exactly one of:

| Section | Existing domain assets Tatvic already sells into |
| ----- | ----- |
| Analytics | GA4, GTM, server-side tagging, consent, attribution, tag audits |
| Cloud and Data Engineering | BigQuery, GCP pipelines, modelling, cost/perf, semantic layers |
| Data Science and AI/ML | Forecasting, anomaly/key-driver, segmentation, MMM, BQML, Vertex |
| Agentic AI | NL-to-SQL / analytics agents, agent platforms, agent eval/governance |
| CRO | Experimentation, A/B stats, personalisation, funnel/UX analysis |

`OUT_OF_SCOPE` never ranks. Adjacent consumer/dev-tools noise never ranks.

## Gate C — Build from existing assets

Prefer problems where version one reuses:

- Existing delivery playbooks, dashboards, or agents.
- Client data already in BigQuery / GA4 / experimentation stacks.
- Skills section teams already bill for (India delivery teams first).

If the first version needs net-new infrastructure outside Tatvic’s stack, score
**effort** low and keep off Top 6 until a section lead sponsors it.

## Gate D — Evidence floor (code-enforced)

- ≥ 3 independent demand items (or 1 strong capability + corroborating demand).
- ≥ 2 distinct authors.
- Evidence level at least **moderate** (2+ source platforms) for CEO Top 6;
  thin (1 platform) is capped and never emailed as a primary recommendation.

## Gate E — Offering angle required

Every ranked row must carry exactly one:

- `upgrade_existing_offering`
- `new_agent_or_offering`
- `cheaper_delivery`

Plus: smallest first version, first client type, one failure-risk line.

## Decision mnemonic

> **Would a Tatvic section lead in India put this on a proposal this quarter?**
> If the honest answer is no, do not rank it for the CEO.
