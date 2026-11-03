# Money and offerings map

> Product intent: [`MASTER_SPECIFICATION.md`](../../MASTER_SPECIFICATION.md).  
> Use this when scoring leverage, commercial signal, and offering angle.

**List hierarchy:** discovery report targets **5–10** opportunities (GOAL). Monthly CEO email includes at most **6** Tatvic-ready clusters. Discover UI spotlights one clearest recommendation. Never pad.

## Named offerings (leverage anchors)

| Offering | Section | Upgrade signals |
| ----- | ----- | ----- |
| BigQuery / analytics agent | Cloud / DS&AI / Analytics | New BQ functions, forecasting, anomaly, key-driver, cost controls |
| CRO Excalibur | CRO | Experiment backlog → live variants, stats ops, personalisation |
| Measurement / tagging programmes | Analytics | SST, consent, reconciliation, tag audits |
| Data engineering retainers | Cloud and Data Engineering | Pipeline reliability, modelling, Looker/Dataform, spend control |
| Forecasting / RCA programmes | Data Science and AI | Native BQ ML / Vertex features that productise delivery |

Leverage score ≥ 4 only when the problem clearly extends one of the above
(or an equivalent billed offering the section already sells).

## Commercial signal ladder

| Score | Evidence |
| ----- | ----- |
| 1 | No money, hiring, or quantified loss |
| 3 | Quantified time/money lost, or “we hired / looking for help” |
| 5 | Explicit intent to buy, budget, RFP, or paid replacement |

Frustration alone is not commercial signal.

## CEO Top 6 rule

The monthly CEO **shortlist** includes at most **6** clusters that are:

1. Tatvic-ready (all gates in `TATVIC_READY.md`).
2. `top10_eligible` true.
3. Score after caps, sorted desc.
4. Prefer one offering angle each when possible; do not fill with thin evidence.

Local artefacts under `data/reports/` are the durable deliverable. Optional email
of that shortlist is **Gmail API only** (MASTER_SPECIFICATION §7.1), explicitly
triggered by an operator, and never implied by a finished discovery run. SMTP is
out of contract.

Dashboard weekly/monthly views are supporting tools.
