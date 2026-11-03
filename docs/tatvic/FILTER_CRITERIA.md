# Tatvic filter criteria — in-scope problems

> Product intent: [`MASTER_SPECIFICATION.md`](../../MASTER_SPECIFICATION.md).  
> Canonical gate detail: `RUBRIC.md`. Precision over volume. Every keep or drop stores a reason code and one sentence. Counting is done by code, not by the model. Borderline items go to REVIEW and are never kept automatically.

## Section scope

| Section | In scope | Out of scope examples |
| ----- | ----- | ----- |
| Analytics | GA4, Tag Manager, server-side tagging, tracking and data quality, consent, attribution, reporting, tag audits | Consumer browser or app issues, ad-platform support |
| Cloud and Data Engineering | BigQuery and the GCP data stack, pipelines, modelling, cost/performance, Dataform, Looker | Flink/Kafka setup, generic DevOps |
| Data Science and AI/ML | Forecasting, anomaly detection, key drivers, segmentation, propensity, MMM, BQML, Vertex AI | Academic research, general coding, GPU infra |
| Agentic AI | LLM agents for analytics/data/marketing; Gemini Enterprise / Agent Platform; NL-to-SQL; agent eval/governance | Consumer chatbots, AI coding assistants, AI news |
| CRO | Experimentation, A/B testing, personalisation, funnel/UX analysis | Generic design, copywriting, SEO |

## Demand gates (order matters)

1. **Hygiene (code)** — DROP_SPAM / DROP_HYGIENE (deleted, &lt;12 words, or a new item outside the fetch budget). Stored opportunities do not expire. See RUBRIC.md section 2.
2. **Section routing (model)** — DROP_CONSUMER, DROP_OUT_OF_SCOPE_TOPIC, DROP_REGULATORY.
3. **Problem test (model)** — KEEP_DEMAND if concrete business problem, organisational context (practitioner **or** budget holder), deliverable, not natively solved, traceable. **N1_IDEA_CONTEXT** for market/idea items — do not drop those as DROP_NOT_PRACTITIONER / DROP_NO_PROBLEM / DROP_OUT_OF_SCOPE_TOPIC. Else REVIEW.
4. **Duplicates** — DROP_DUPLICATE; Top 10 needs ≥3 independent authors.

## Capability items

KEEP_CAPABILITY for new/changed features and deprecations. DROP_HYGIENE for bug fixes / minor UI.

## Reason codes

See RUBRIC.md §5 including `N1_IDEA_CONTEXT`, `DROP_HYGIENE` and `DROP_REGULATORY`.
