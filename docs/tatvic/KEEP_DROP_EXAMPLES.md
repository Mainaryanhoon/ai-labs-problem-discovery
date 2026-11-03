# Keep / drop examples (Tatvic POV)

> Canonical codes: `RUBRIC.md`. Product intent: [`MASTER_SPECIFICATION.md`](../../MASTER_SPECIFICATION.md).  
> Illustrative cases for model calibration and automated tests. Not live data.

| Input | Expected code | Section / note |
| ----- | ----- | ----- |
| Chrome PDF filename question | `DROP_CONSUMER` | Outside Tatvic |
| Flink truststore-in-JAR question | `DROP_OUT_OF_SCOPE_TOPIC` | Unless stream eng joins Cloud section |
| Founder/CMO on India D2C CAC or RTO (no GA4 keywords) | `N1_IDEA_CONTEXT` | Lane 1 lead — not DROP_NOT_PRACTITIONER |
| “GA4 purchases don’t match store orders; we reconcile weekly by hand” | `KEEP_DEMAND` | Analytics, eCommerce — money path: reconciliation service / agent |
| “BigQuery bill doubled after adding scheduled queries” | `KEEP_DEMAND` | Cloud and Data Engineering — cost optimisation offering |
| Beginner asking how to install GA4 | `DROP_NO_PROBLEM` | Curiosity / how-to |
| Release note: new forecasting function in preview | `KEEP_CAPABILITY` | Data Science and AI, preview maturity |
| “Anyone running server-side tagging for a bank app?” | `REVIEW` | Section clear, ask vague |
| “How do lenders interpret a new central-bank circular?” | `DROP_REGULATORY` | Law/regulation interpretation, not deliverable |
| “Turning our A/B test backlog into live variants takes weeks” | `KEEP_DEMAND` | CRO — Excalibur upgrade path |
| “We need daily demand forecasts for thousands of SKUs without a model per SKU” | `KEEP_DEMAND` | Data Science and AI — forecasting programme |
| BigQuery adds native anomaly detection / key-driver functions | `KEEP_CAPABILITY` | Affects RCA / forecasting delivery cost |
| Job post for GA4 analyst | `DROP_SPAM` / hygiene drop | Not a problem statement |
| Vendor bug that only Google can fix, no service wrapper | `DROP_NOT_SOLVABLE` | Or `NATIVE_SOLVED` if already shipped |

## Five-test quick check

A problem statement is in scope only if all are true:

1. Concrete friction / cost / workaround.
2. Organisational or budget-holder context (practitioner voice is valid, not required).
3. Maps to one Tatvic section.
4. Tatvic can deliver service, agent, automation, or analysis.
5. Traceable quote exists.

Casual wording alone is **not** a reject reason.
