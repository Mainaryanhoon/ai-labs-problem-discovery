# AI Labs Problem Discovery — implementation blueprint
Version 1.0 | 18 October 2026
Repository: `Mainaryanhoon/ai-labs-problem-discovery`
Product reference: Razorpay Fix My Itch screenshots, used as design inspiration only.

## 1. Goal
Collect public and voluntary signals, remove noise, extract normalized problem statements, merge similar problems across sources, rank clusters transparently and show evidence behind the Top 10. The ranked unit is a problem cluster, not a post. This is our own implementation; Razorpay's private code and exact score formula are not known.

## 2. Principles
- Evidence first: retain source URL/ID, platform, timestamps, collection method and provenance where permitted.
- Separate raw source records, extracted candidates, clusters and scores.
- Cheap validation and deduplication before paid model calls.
- Structured, schema-validated AI output; store model, prompt and schema versions.
- Explainable ranking, visible uncertainty and human review.
- Respect platform terms, privacy, quotas, rate limits and retention policies.
- Keep local development first; request company approvals early if provisioning is slow.
- Test without consuming API credits; use provider boundaries and saved fixtures.

## 3. Product UX
Inspired by the supplied Fix My Itch screenshots:
- Dark canvas, high-contrast type, oversized section headings, electric-blue accent.
- Navigation: Insights, Top 10 Problems, All Problems, Mission.
- Top 10 cards with category chips and concise problem statements.
- All Problems search, filters, sort, industry/category metadata and expandable details.
- Problem detail: evidence, independent sources, affected users, workaround, score breakdown and uncertainty.
- Manual submission form.
- Reviewer queue: accept, reject, merge, split and relabel.
Visual resemblance is secondary to trustworthy evidence and ranking.

## 4. Architecture
```text
Approved source adapters + manual submissions
 -> raw source records/provenance
 -> normalization and validation
 -> cheap noise filters
 -> exact deduplication
 -> semantic near-duplicate detection
 -> Gemini structured extraction
 -> validated problem candidates
 -> cross-source clustering
 -> human review / merge / split
 -> severity + frequency + whitespace + TAM signals
 -> explainable scores and uncertainty
 -> API + UI + exports + monitoring
```
Components: source adapters, ingestion, filters, AI extraction, embeddings/clustering, scoring, reviewer workflow, API/UI, run monitoring and cost tracking.

## 5. Data model destination
The starter implements only `submissions`. Expand through migrations into:
- `source_records`: source, source ID, canonical URL, published/collected times, text, language, content hash, collection method, permitted raw payload, status, rejection reason, retention.
- `problem_candidates`: source record, normalized statement, affected user, context, workaround, category, industry, tags, genuine-problem flag, scores, model/prompt/schema versions, review status.
- `problem_clusters`: canonical statement, category, industry, status, algorithm/version and representative candidate.
- `cluster_memberships`: cluster, candidate, similarity, membership reason and version.
- `opportunity_scores`: component scores, TAM assumptions/method, source diversity, confidence, uncertainty, total, rank and score version.
- `review_events`: reviewer, action, target, reason and timestamp.
- `pipeline_runs`: source, run times/status, counts, errors, model calls and estimated cost.

## 6. Extraction contract
Each candidate should capture:
- One-sentence neutral problem statement.
- Affected person/business and context.
- Current workaround.
- Category, industry and tags.
- Genuine-problem flag/rejection reason.
- Urgency/severity signal 1–5.
- Frequency signal 1–5, only a single-record signal until aggregated.
- Evidence strength 1–5.
- Uncertainty notes, source reference, model and prompt version.
A single post does not establish population-level frequency or TAM.

## 7. Noise reduction
1. Validate schema, empty text and length.
2. Normalize URL and platform IDs.
3. Exact dedupe using source ID, canonical URL and normalized text hash.
4. Configurable rules for spam, link-only, obvious promotion, irrelevant language/content.
5. Embedding-based near-duplicate matching.
6. Gemini classification for ambiguous/valuable candidates.
7. Save rejection reason where permitted for audit and quality evaluation.
Do not blindly drop every short post; evaluate false positives.

## 8. Cross-source clustering
- Embed normalized problem statements and selected context.
- Retrieve nearest neighbors instead of comparing every pair at scale.
- Select similarity thresholds from a reviewed labeled sample.
- Consider affected user, context and category.
- Preserve multiple independent source records as evidence under one cluster.
- Support reviewer merge/split and store history.
- Version embeddings and clustering; evaluate cluster precision/recall.
- Do not count cross-posts or syndicated copies as independent corroboration.

## 9. Ranking method
Dimensions inspired by Fix My Itch: Severity, Frequency, Market Whitespace and TAM. Exact proprietary weights are not public; we must define and document ours.
- Severity: impact in time, money, risk or lost opportunity.
- Frequency: aggregate records adjusted for duplicates, source quality, time window and source diversity.
- Market whitespace: how well existing products serve the affected segment; requires evidence, not just model opinion.
- TAM: defensible addressable-user/account assumptions × annual value; cite assumptions and show low/base/high scenarios.
Normalize components to 0–100, version weights, display each component, include uncertainty and a minimum-evidence gate. Test ranking stability when weights change. Do not let persuasive LLM prose substitute for evidence.

## 10. Source waves
Candidate inventory from the planning file: Reddit, Hacker News, Stack Exchange, government tenders, paid tender aggregators, app reviews, G2/Capterra, X, LinkedIn, Quora, YouTube, domain forums, internal CRM, Lusha, manual form and client notes. Candidate does not mean approved.
- **Wave 0:** manual submission and permitted JSON/CSV import; fixtures for tests.
- **Wave 1:** choose one approved source after access/terms/cost review. Reddit official API, Hacker News or Stack Exchange may be candidates depending on scope. Apify may be used after a specific Actor is reviewed.
- **Wave 2:** app reviews, tenders, G2/Capterra, YouTube, X, Quora and niche forums as approvals and source quality justify.
- CRM/Lusha: do not ingest until company purpose, privacy, retention and access approval are explicit.
- LinkedIn: do not depend on unauthorized scraping; use voluntary/manual input or approved official access.
Every adapter must document fields, access method, terms review, quotas/cost, reliability, cadence, output schema, fallback, owner and open questions.
Apify is a tool, not blanket permission to scrape every target.

## 11. AI provider/cost
Use official Google Gen AI SDK for Gemini initially; provider service isolates model calls. Validate responses with Pydantic. Track model/prompt version, latency, status and estimated cost. Cache by content hash + prompt/model version. Bound batch size and retries. Test using stubs. Maintain a human-reviewed evaluation set. Consider Vertex AI later if company governance, IAM, billing or data policy requires it; direct Gemini API and Vertex AI are distinct deployment choices.

## 12. API/UI plan
API: submissions, ingestion, cluster search/filter/sort, cluster detail/evidence, Top 10, review actions, pipeline status, source health and CSV/JSON export.
UI pages:
1. Insights: counts, source coverage, fresh signals, pipeline health.
2. Top 10: cards, category, score, confidence.
3. All Problems: search, filters, sort and pagination.
4. Problem Detail: evidence, sources, score breakdown, uncertainty.
5. Submit a Problem: text, optional URL/context and consent notice.
6. Review Queue: accept/reject/relabel/merge/split with reason.
7. Source Health/Admin: last run, counts, errors, cost and access-review status.
Suggested UI after API contracts stabilize: React + TypeScript + Vite; backend FastAPI. Responsive, accessible black/white/electric-blue design.

## 13. Tests and evaluation
Automated tests for schemas, API/persistence, URL normalization, dedupe, filters/rejection reasons, missing keys/provider failures, score math, collector contracts and access controls.
Human-reviewed evaluation set should label genuine problem, normalized statement, category, same/different-problem pairs, duplicate groups, severity and evidence quality.
Metrics: classification precision/recall, cluster quality, reviewer overturn rate, source diversity, cost per accepted candidate and ranking stability.

## 14. Security and privacy
- Never commit `.env`, keys, tokens, cookies or personal datasets.
- Use least privilege and secret storage in deployment.
- Minimize author identifiers and sensitive data.
- Follow retention/deletion requirements and source terms.
- Rate-limit public submissions before deployment.
- Log counts/IDs, not unnecessary sensitive text.
- Keep lineage from ranked cluster to source evidence.

## 15. GCP plan
Work locally first but ask about approvals early. Confirm whether to use an existing approved project or a separate dev project, billing owner and allowed data classification.
Potential later services: Cloud Run (API/worker), Cloud Scheduler/Workflows (scheduled runs), Cloud Storage (approved raw artifacts), BigQuery (analytics) or Cloud SQL/PostgreSQL (transactional data), Secret Manager, Cloud Logging/Monitoring, and Vertex AI only if selected. Do not provision everything now. Set budget alerts and quotas before running jobs.

## 16. Milestones and acceptance criteria
- **M0 — vertical slice:** local app runs; health endpoint works; submission persists; real Gemini output is schema-validated; failures visible; secrets excluded; tests pass.
- **M1 — ingestion:** source record model, migrations, first approved collector, idempotency, provenance and run metrics.
- **M2 — quality:** filters, rejection reasons, exact dedupe, evaluation fixtures, caching and cost counters.
- **M3 — clustering:** embeddings, neighbor retrieval, membership/versioning, merge/split, quality evaluation.
- **M4 — ranking/review:** score components, uncertainty, TAM assumptions, human review and explainable Top 10.
- **M5 — UI:** Insights, Top 10, All Problems, detail, submission and review pages.
- **M6 — GCP:** approved project, secrets, deployment, scheduler, monitoring, quotas and recovery playbook.
- **M7 — iteration:** measure quality/cost, improve prompts or train a classifier only if evaluation justifies it, add sources one at a time.

## 17. Definition of done for M0
A fresh developer can run it from the README; submissions are persisted before model calls; Gemini output is validated and versioned; failures do not erase input; tests do not call live services; no secrets are tracked; score signals are not misrepresented as market truth.

## 18. Stakeholder decisions
1. Target user/customer segment and industries?
2. Consumer pain, SMB/business pain, internal ideas or all?
3. Which sources are in scope for first release?
4. Who owns access review and human labels?
5. Direct Gemini API or company-managed Vertex AI?
6. What data may be stored locally/in GCP?
7. TAM definition and acceptable assumptions?
8. Who approves/rejects opportunities for display?
9. Expected volume, refresh cadence and budget?
10. Deployment and branch/PR policies?

## 19. First actions
- Confirm repository root and branch.
- Merge these starter files without overwriting project-specific README instructions.
- Create venv/install dependencies.
- Add API key locally to `.env`.
- Run health, submission and tests.
- Analyze one manual sample and inspect output.
- Select the first approved live source and exact Apify Actor/API.
- Add the collector only after reviewing permissions, schema and estimated charges.
