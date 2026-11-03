# Tatvic opportunity scoring (buying filters)

> Product intent: [`MASTER_SPECIFICATION.md`](../../MASTER_SPECIFICATION.md).  

**Weight version:** `v0.1` (commercial-first; matches `app/tatvic_scoring.py`)  
**Owners:** Khilav Joshi and Ravi Pathak.  
Canonical formula detail: `RUBRIC.md`.  
Inactive: `v0.2-commercial`. Weekly leads ranking: `v0.3-leads`.

Missing or invalid qualitative components (readiness, fit, commercial,
effort, defensibility) aggregate to **1** with `"no evidence"`. Effort uses the
same rule.

**india_relevance** is capped by verified stored-text markers (`india_cap_from_text`).
The model cannot exceed that cap.

**N1_IDEA_CONTEXT** is a valid filter reason code: market/idea items are kept as
leads/context, not dropped as `DROP_NOT_PRACTITIONER` / `DROP_NO_PROBLEM` /
`DROP_OUT_OF_SCOPE_TOPIC`.

**Commercial bypass:** `commercial_signal >= 4` or a C-suite / budget-holder buyer
skips `is_product_ready()` Tatvic-offering keyword gates.

## Formula

`score = sum(weight × (component − 1) ÷ 4)` from 0 to 100. Each component is scored 1–5.

| Filter | Weight | Question |
| ----- | ----- | ----- |
| commercial_signal | 35 | Is there evidence someone pays, hires, budgets, or loses money? |
| demand_strength | 20 | How many independent items, across how many sources, and is it rising? |
| india_relevance | 15 | Do verified stored-text markers support India? |
| section_client_fit | 10 | Is it squarely inside a section and relevant to clients like ours? |
| capability_readiness | 10 | Is the technology available now and usable by Tatvic? |
| effort_to_first_version | 5 | How small can version one be? Higher means smaller. |
| defensibility | 5 | Is a vendor unlikely to ship this natively soon? |

## Demand anchors (code only)

| Score | Rule |
| ----- | ----- |
| 1 | A single item |
| 2 | 2 to 3 items |
| 3 | 4 to 9 items, or items from two sources |
| 4 | 10 to 24 items, or 4 to 9 items across 3 or more sources |
| 5 | 25+ items across 3+ sources, and recent 30d count > previous 30d |

If there is no previous-window data, rising is false and demand cannot exceed 4.

## Eligibility and caps

- Lane 2 Top 10 needs ≥3 independent items, ≥2 authors, and an in-scope section (commercial bypass does not skip independence for the product-ready list).
- Single source platform: score capped at 60.
- Evidence level: Strong (3+), Moderate (2), Thin (1).

## Worked example

commercial 4, demand 4, india 3, fit 4, readiness 4, effort 5, defensibility 1 → **68.8**.
