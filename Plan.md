AI Labs Problem Discovery — Master Implementation Brief
0. Mission
Build a working, local-first problem and opportunity discovery engine that can eventually serve multiple publisher/client verticals.
The product must discover genuine unmet needs from public online discussions, extract structured evidence, merge reports about the same underlying problem, avoid counting duplicates as demand, and rank promising opportunities for human investigation.
This is NOT merely a social-listening dashboard, sentiment-analysis tool, or keyword-frequency counter.
Operating principle
BUILD THE WORKING PRODUCT FIRST. POLISH ITERATIVELY.
The current codebase already has a working FastAPI foundation and 28 passing tests. Do not restart the architecture, spend excessive time inspecting files, or create endless mock-only exercises.
Inspect the relevant files just enough to understand their interfaces, implement complete vertical slices, run the existing test suite, and fix regressions. Use real source data and real integrations where configured, with mocks for deterministic regression tests.
Do not claim an integration is verified until it has actually been exercised.

1. Current project context
Repository:
ai-labs-problem-discovery/ai-labs-problem-discovery
Environment:
Windows 11
Python 3.11
Virtual environment: .venv
FastAPI and SQLAlchemy
SQLite local database
Gemini for structured analysis and embeddings
Apify configured for future social-source ingestion
Use Windows CMD-compatible commands.
Current known implementation includes:
app/main.py
app/config.py
app/models.py
app/schemas.py
app/cluster_schemas.py
app/db.py
app/ingestion.py
app/clustering.py
app/ranking.py
app/services/gemini.py
app/social_ingestion.py was referenced by main.py; verify the file exists and inspect its actual implementation before changing it.
Tests include API, clustering, ranking, and opportunity-scoring coverage.
Latest user-reported baseline:
Full test suite: 28 passed.
API tests: 5 passed.
One non-blocking Starlette/httpx deprecation warning.
Do not assume these test counts remain unchanged after implementation. Rerun tests at the end.
Existing sources and integrations
Hacker News via Algolia: ingestion endpoint exists and was reported working.
Stack Exchange: ingestion endpoint exists and was reported working.
Apify: token is configured and was verified previously.
Reddit: intended Apify integration, not yet confirmed end to end.
X/Twitter: intended Apify integration, not yet confirmed end to end.
Gemini: analysis has succeeded previously, but quota/rate limits have occurred. Handle them explicitly.
Gemini embeddings: used for semantic clustering; missing credentials or embedding failures must not silently trigger a different matching algorithm.
Existing ingestion endpoints:
POST /api/ingestion/hacker-news
POST /api/ingestion/stack-exchange
Preserve these working routes and their existing request contracts unless a backward-compatible improvement is necessary.
Potential Apify actor IDs previously considered:
anyxsolutions~reddit-scraper
apidojo~tweet-scraper
Treat these as UNVERIFIED candidates, not confirmed dependencies. Verify actor availability, input schema, output schema, cost behavior, and account access before relying on them. If a candidate is unsuitable, document that and use a verified alternative.
Never expose API tokens in logs, responses, source code, or committed files.

2. Required product architecture
The complete pipeline must be:
Source collection
→ canonical source record
→ persistent submission
→ structured problem analysis
→ exact duplicate detection
→ semantic clustering
→ evidence aggregation
→ opportunity scoring
→ API response for human review.
The implementation may retain the current project structure. Do not introduce an unnecessary framework or rewrite the database layer.
Use the existing SQLAlchemy models and Pydantic schemas where possible. Add models or migrations only when a genuine capability requires them.
Data integrity rules
Every submission must preserve its original source, source URL when available, text, timestamp when available, and provenance.
A submission must not be treated as a verified independent person merely because it comes from a different URL or platform.
Copies, reposts, syndicated content, and near-identical posts must not inflate independent demand.
Similar but independently written reports can be evidence of recurrence, but their independence must be represented conservatively.
Semantic similarity is not proof that two reports describe exactly the same problem.
Uncertain cluster matches must be reviewable.
The system must never invent willingness to pay, market size, customer counts, or purchase intent.
Scores are prioritization heuristics, not proof of product-market fit.
Preserve the source text and source link so every ranking can be investigated.

3. Phase A — Finish structured problem analysis
Primary files:
app/services/gemini.py
app/schemas.py and the actual analysis-output schema
app/main.py
relevant tests
A1. Review and finalize the extraction schema
The structured analysis should represent, where supported by the existing code:
Is this a genuine problem?
Signal type: pain point, workaround, unmet need, feature request, tool request, buying signal, general discussion, or noise.
Problem statement.
Affected user/persona.
Current workaround or existing solution, if stated.
Reported impact or consequence.
Urgency, 1–5.
Frequency signal, 1–5.
Evidence strength, 1–5.
Willingness-to-pay signal: none, suggested, or explicit.
Evidence quotes copied from the source.
Analysis limitations or uncertainty where useful.
Retain the existing field names when possible because ranking code and stored analysis JSON already depend on them.
Do not add redundant fields merely to make the schema longer. Add a field only when a downstream capability needs it.
A2. Make extraction conservative and usable
Ensure the prompt distinguishes:
A real pain point from a casual opinion.
A feature request from a demonstrated unmet need.
A workaround from a hypothetical suggestion.
An explicit purchase or payment statement from an inferred commercial opportunity.
Popularity from problem severity.
Repeated copies from independent reports.
Require the model to avoid unsupported factual claims. Evidence quotes must be traceable to the original post and must not be fabricated.
Do not equate a high engagement count with a high frequency score unless the methodology explicitly supports that inference.
A3. Harden error handling
Preserve the current analysis states and error handling.
Verify:
Valid model response → completed.
Invalid JSON or schema mismatch → controlled analysis error.
Missing Gemini configuration → clear configuration error.
Rate limit → retryable/pending state.
Transient provider error → bounded retry where appropriate.
Permanent invalid response → recorded failure with diagnostic information.
Batch analysis stops or backs off on rate limiting rather than repeatedly burning quota.
Do not store a submission as completed if its analysis failed validation.
Do not log API keys or entire sensitive configuration objects.
A4. Add batch analysis capability only where needed
Preserve POST /api/submissions/{submission_id}/analyze and POST /api/submissions/analyze-pending.
Make sure the batch operation:
Respects its configured limit.
Records individual outcomes.
Stops or backs off when rate limited.
Does not lose completed work when a later item fails.
Can be rerun safely.
A5. Acceptance criteria
Existing analysis schema and ranking consumers remain compatible.
Representative examples produce structurally valid analysis.
Noise is not automatically treated as an opportunity.
Explicit and suggested willingness-to-pay remain distinct.
Failed analysis cannot masquerade as successful analysis.
Existing tests pass.
At least one small, controlled batch can be analyzed with the configured Gemini account when quota is available.
Complete Phase A and summarize actual changes before moving on. Do not wait for exhaustive model evaluation.

4. Phase B — Make clustering and deduplication reliable
Primary files:
app/clustering.py
app/models.py
app/cluster_schemas.py
app/main.py
clustering tests
B1. Exact duplicate detection
Use normalized text and canonicalized URLs where available.
Verify:
URL schemes and hosts are normalized.
Tracking parameters and fragments are removed where safe.
Empty URLs are handled safely.
Missing text does not crash matching.
Same canonical URL is not counted repeatedly.
Identical normalized text is not counted repeatedly.
Different URLs do not automatically mean independent evidence.
Keep a stable, deterministic duplicate-selection policy. Prefer a consistent original/earliest canonical record when deciding which record represents duplicate content, without deleting raw submissions.
Never delete source submissions merely because they are duplicates. Preserve provenance.
B2. Semantic clustering
Use the existing embedding-based implementation.
Retain the current thresholds as initial heuristics:
Automatic-match threshold: 0.78.
Review threshold: 0.60.
Do not claim these thresholds are calibrated. They must later be evaluated against labeled examples.
Expected behavior:
Similarity above the automatic threshold → candidate match, with a clear relationship and provenance.
Similarity between review and automatic thresholds → review required.
Similarity below the review threshold → candidate for a separate cluster.
Embedding failure → explicit error; no silent lexical fallback.
Reprocessing the same submission → no duplicate evidence attachment.
An existing evidence relationship → no accidental duplicate cluster creation.
Inspect whether the current implementation sets is_independent=False for every automatically attached report. If so, do not treat that as a permanent assumption that every report is non-independent. Represent the distinction between automated attachment and verified independence appropriately, without inventing independence.
B3. Cluster lifecycle and reviewability
Preserve:
POST /api/clusters
GET /api/clusters
GET /api/clusters/{cluster_id}
POST /api/clusters/{cluster_id}/evidence
POST /api/submissions/{submission_id}/cluster
Preserve the ClusterDetail response contract:
Cluster fields.
Attached evidence.
evidence_count.
independent_evidence_count.
Do not reintroduce opportunity aggregation into the cluster-detail endpoint.
Keep the opportunity endpoint separate:
GET /api/clusters/{cluster_id}/opportunity
Handle already-clustered submissions, duplicate submissions, and missing clusters predictably.
B4. Acceptance criteria
Repeated clustering does not duplicate evidence.
Exact duplicates cannot inflate opportunity counts.
Similar reports can be grouped without deleting raw records.
Ambiguous matches are reviewable.
Embedding failures are visible.
Cluster detail and opportunity scoring remain separate.
Existing tests pass.

5. Phase C — Finish opportunity scoring
Primary files:
app/ranking.py
opportunity endpoint in app/main.py
tests/test_ranking.py
tests/test_opportunity_scoring.py
C1. Preserve transparent scoring
The existing score uses:
Urgency: 30%.
Frequency signal: 25%.
Evidence strength: 20%.
Multi-source bonus: 5 points.
Verified independent-source bonus: 10 points.
Explicit willingness-to-pay bonus: 10 points.
Suggested willingness-to-pay bonus: 3 points.
Missing evidence quotes penalty: 10 points.
Output constrained to 0–100.
Keep these weights as the initial baseline unless the current implementation or a demonstrated defect requires a correction. Do not silently change the methodology.
Validate every input. Booleans must not be accepted as numeric ratings. Clamp ratings to valid ranges or reject malformed data consistently.
C2. Fix evidence aggregation semantics
The opportunity endpoint must distinguish:
Raw attached evidence records.
Successfully analyzed records.
Unique records retained for scoring.
Exact duplicates excluded.
Unique platforms.
Recorded independence flags.
Verified independent evidence.
Do not conflate any of these counts.
The current implementation passes verified_independent_sources=0 into the ranking function. Do not award the independence bonus until there is a defensible verification mechanism.
An is_independent=True flag by itself is not sufficient proof that a distinct person independently reported the problem.
If the current aggregate logic uses any() to select the highest willingness-to-pay signal across a cluster, preserve that as an aggregate signal only if the output clearly explains that it comes from at least one included record. Do not imply that all reports demonstrate buying intent.
C3. Improve score explanations
Every scored opportunity should provide:
Score.
Evidence level.
Reasons for the score.
Important limitations.
Number of raw attached records.
Number of analyzed records.
Number of unique records used.
Duplicate exclusions.
Source-platform count.
Recorded and verified independence counts, clearly distinguished.
Commercial signal and its limitations.
When there is insufficient analyzed evidence:
Return a null score.
Return an explicit insufficient-evidence status.
Do not manufacture a score from missing fields.
Keep all record counts accurate.
C4. Acceptance criteria
Duplicates do not inflate scores through evidence counts.
General discussion/noise is not promoted as a genuine opportunity.
Missing evidence is handled conservatively.
Willingness-to-pay is never inferred as explicit without evidence.
Verified independence is not claimed without verification.
Scores are reproducible for the same stored inputs.
The API explains why an opportunity ranks highly and what remains unknown.

6. Phase D — Complete ingestion across confirmed and planned sources
Primary files:
app/ingestion.py
app/social_ingestion.py, if present
app/config.py
app/main.py
ingestion tests
D1. Hacker News via Algolia — existing source
Preserve and finalize the existing Hacker News ingestion route.
Ensure:
Search terms or queries can be configured.
Results are normalized into the canonical internal submission representation.
Original post URLs are retained.
Timestamps and author names are retained when available.
Missing optional fields do not crash ingestion.
Pagination and result limits are handled.
Repeated runs do not create uncontrolled duplicate records.
Source failures are reported clearly.
Do not replace the existing working integration without a specific reason.
D2. Stack Exchange — existing source
Preserve and finalize the existing Stack Exchange route.
Ensure:
Site and search configuration are handled appropriately.
Question title and body are preserved.
Original question URL, author, and timestamps are retained when available.
API pagination and throttling are handled.
API quotas are respected.
Missing optional fields do not crash ingestion.
Repeated runs are idempotent at the source-record level.
D3. Reddit via Apify — planned integration
Verify the actual selected Actor's:
Identifier and availability.
Required input schema.
Search capabilities.
Output schema.
Pagination or maximum-results behavior.
Pricing/usage behavior.
Access requirements.
Then implement the adapter using the verified contract.
Normalize available fields:
Post ID.
Text/body.
Title.
Author.
Permalink.
Creation timestamp.
Community/subreddit.
Engagement metrics where available.
Source platform.
Handle deleted, empty, restricted, or malformed records.
Do not assume every Reddit scraper returns the same JSON structure.
D4. X/Twitter via Apify — planned integration
Apply the same verification and normalization requirements.
Preserve available:
Post/tweet ID.
Text.
Author/handle.
Canonical post URL.
Timestamp.
Engagement metrics.
Relevant query/search provenance.
Do not use engagement metrics as independent demand evidence.
Do not start uncontrolled live Actor runs. Use bounded result counts and confirm the expected cost before running a live integration test.
D5. Canonical source representation
Create or reuse a small, stable internal representation shared by all ingestion adapters.
It should contain:
source_name
source_id where available
source_url
text
submitted_by where available
created_at where available
language where available
metadata for source-specific optional information
Do not break the existing SubmissionCreate contract unnecessarily. If the database needs a new source ID or metadata field for reliable deduplication, add it with a safe migration strategy. Do not assume Base.metadata.create_all() will migrate existing SQLite tables.
D6. Ingestion reliability
Implement:
Bounded request timeouts.
Clear errors for authentication failures, rate limits, and provider errors.
Bounded retries with backoff for transient errors.
Result limits.
Pagination where supported.
Idempotent storage behavior.
Source-specific parsing functions.
Structured logs without credentials.
A useful summary of fetched, inserted, duplicate, and rejected records.
Avoid silently swallowing source failures.
D7. API and configuration
Use separate source configuration fields where appropriate. Do not reuse a single Actor ID for unrelated providers.
Keep credentials in environment variables or the existing settings mechanism.
If adding endpoints, make them consistent with the existing ingestion router conventions.
Ensure live ingestion cannot accidentally trigger expensive unbounded jobs.
D8. Acceptance criteria
Hacker News ingestion still works.
Stack Exchange ingestion still works.
Reddit ingestion works with the selected, verified Apify Actor.
X ingestion works with the selected, verified Apify Actor.
All sources produce consistent internal submission records.
Repeated ingestion does not inflate the database with uncontrolled duplicates.
Failures are visible and retryable where appropriate.
Provider credentials are never returned to clients.
Each live provider is explicitly marked verified only after a successful bounded integration run.
If an external provider is blocked by credentials, account access, pricing, or changed Actor schemas, finish the adapter and deterministic tests, document the precise blocker, and continue with the other phases. Do not falsely claim a live integration succeeded.

7. Phase E — End-to-end orchestration
After Phases A–D are implemented, make the complete workflow easy to run.
Required workflow:
Ingest a bounded batch from selected sources.
Persist normalized submissions.
Analyze pending submissions.
Cluster completed analyses.
Generate or refresh opportunity summaries.
Return a summary of each stage's outcomes.
Reuse the existing API endpoints and service functions where possible.
If a new orchestration endpoint is needed, create one that invokes existing functions instead of duplicating their logic.
The workflow must:
Be rerunnable.
Avoid duplicate submissions and evidence attachments.
Report partial success.
Stop or back off when providers are rate limited.
Avoid reanalyzing completed records unnecessarily.
Avoid expensive external calls for already processed data unless explicitly requested.
Report counts of fetched, inserted, analyzed, failed, clustered, duplicated, and skipped records.
Do not create a large asynchronous job system unless current workload requires it. A bounded local-first workflow is sufficient for the next milestone.

8. Phase F — Operator-facing opportunity review API
Before building a frontend, make the backend easy to inspect.
Expose or extend APIs so a user can:
List opportunity clusters.
Sort/filter by score, evidence level, category, and status where practical.
Retrieve a cluster's evidence and source URLs.
Retrieve the opportunity score and its explanation.
Identify duplicates excluded from scoring.
Distinguish unverified claims from verified evidence.
Inspect incomplete analysis or failed records.
Re-run clustering or scoring safely when needed.
Keep response schemas explicit. Add pagination for growing lists. Do not expose stack traces, credentials, or internal exception objects.

9. Phase G — Build the UI after the engine works
Once the complete pipeline is usable, build a straightforward review interface.
Primary views:
Opportunity list ranked by score.
Opportunity detail with problem statement, evidence, source links, and limitations.
Cluster evidence review.
Ingestion run summary.
Failed/pending analysis queue.
Filters for source, category, evidence level, and status.
The UI should communicate uncertainty. Avoid presenting a score as a probability of commercial success.
Do not spend time polishing visual design before the end-to-end workflow works.

10. Phase H — Production readiness and GCP
Only after the local pipeline is working:
Add or finalize safe database migrations.
Separate development and production configuration.
Move secrets to an appropriate secret-management mechanism.
Add structured logging and error monitoring.
Set timeouts and quotas.
Ensure ingestion jobs are bounded and repeatable.
Evaluate whether SQLite remains appropriate or a managed relational database is needed.
Package the application.
Deploy to the selected GCP environment.
Verify health checks, persistence, and external API connectivity.
Document deployment and recovery procedures.
Do not deploy a pipeline that loses source provenance or inflates evidence counts.

11. Benchmarking and quality evaluation — required standard
We want a rigorous benchmark inspired by strong production ML and information-retrieval engineering, not a large number of shallow unit tests.
Create a small, versioned, labeled evaluation dataset. Start with approximately 100–200 real, representative source records across the available sources, including:
Genuine pain points.
Feature requests.
Workarounds.
Buying signals.
General discussion.
Noise.
Exact duplicates.
Near-duplicates.
Similar wording with different underlying problems.
Independent reports about the same underlying problem.
Contradictory evidence.
Use human-reviewed labels. Do not use the model's own output as the ground truth.
Keep raw source IDs/URLs and evaluation labels separate from model-generated outputs. Respect provider terms and avoid collecting unnecessary personal information.
Benchmark A: Problem classification
Measure:
Precision, recall, and F1 for genuine-problem detection.
Macro-F1 across signal categories.
Confusion matrix.
False-positive rate for noise/general discussion.
Accuracy of willingness-to-pay labels, especially false explicit-buying-signal claims.
Evidence-quote traceability.
Primary business risk: generating convincing but unsupported opportunities.
Report overall metrics and per-source metrics where sample size permits.
Benchmark B: Duplicate detection
Build labeled pairs:
Exact duplicates.
Canonical-URL duplicates.
Near-duplicate copies.
Distinct reports about the same problem.
Unrelated reports.
Measure precision and recall for duplicate detection.
Track false-positive merges separately from missed duplicates. False merges can destroy useful distinctions; missed duplicates can inflate demand.
Benchmark C: Clustering
Use a labeled subset of related/unrelated reports.
Measure:
Pairwise precision and recall.
Pairwise F1.
B-cubed precision/recall/F1 or another suitable clustering metric.
Number of unnecessary cluster merges.
Number of fragmented duplicate clusters.
Percentage of ambiguous matches routed for review.
Repeat-run idempotency.
Manually inspect a sample of good matches, bad matches, and borderline cases.
Do not tune similarity thresholds solely to maximize a single metric.
Benchmark D: Opportunity ranking
Create a small human-reviewed ranking set with a clear rubric. The labels should reflect evidence-backed investigation priority, not speculative revenue potential.
Measure:
NDCG@5 and NDCG@10.
Precision@5 where a binary relevance label is available.
Ranking stability when exact duplicates are added.
Ranking stability when copied posts are added.
Score behavior with insufficient evidence.
Whether explicit willingness-to-pay claims are properly supported.
Whether the top-ranked candidates have traceable evidence.
Run a counterfactual duplicate-inflation test: duplicate a set of existing records and verify that the system does not incorrectly increase independent evidence or reward copied content.
Benchmark E: End-to-end system reliability
Measure:
Ingestion success rate.
Percentage of records rejected due to malformed source data.
Analysis completion/failure/rate-limit counts.
Clustering failure counts.
Duplicate rate.
Number of records lost between stages.
Runtime per bounded batch.
Gemini and Apify cost/usage where available.
Repeat-run idempotency.
API error rate on the tested workflow.
Do not fabricate latency or cost measurements. Record actual observations.
Benchmark F: Regression suite
Keep the existing automated tests and add targeted tests for newly implemented behavior.
Test:
Happy paths.
Malformed provider records.
Empty datasets.
Duplicate ingestion.
Rate limits.
Provider outages.
Invalid analysis output.
Embedding failure.
Duplicate clustering.
Insufficient scoring evidence.
API response contracts.
Idempotent reruns.
Use isolated test databases. Do not allow tests to contaminate the real local SQLite database.
Mocks should make automated tests deterministic. Real integration runs should be a separate, bounded validation step.
Benchmark reporting
Create a reproducible evaluation command and a report that includes:
Dataset version and size.
Label definitions.
Model and prompt version.
Embedding model and clustering thresholds.
Classification metrics.
Deduplication and clustering metrics.
Ranking metrics.
Failure cases.
Runtime and provider cost where measured.
Known limitations.
Never report a metric without stating what dataset and labels it came from.
Initial quality gates
Use these as initial targets for the labeled evaluation set, not as claims about current performance:
Genuine-problem detection precision: at least 0.85.
Genuine-problem detection recall: at least 0.75.
Macro-F1 for signal classification: at least 0.75.
Duplicate-detection precision: at least 0.95 for exact duplicates.
Duplicate-detection recall: at least 0.90 for exact duplicates.
Pairwise clustering precision: at least 0.85.
Pairwise clustering recall: at least 0.75.
No unsupported explicit willingness-to-pay claims in the manually reviewed acceptance sample.
No score increase attributable solely to duplicated copies.
No duplicate evidence attachment on repeated processing.
No loss of source provenance in the end-to-end workflow.
These are provisional engineering gates. If the labeled sample is too small for a meaningful conclusion, report the limitation instead of declaring success.

12. Implementation workflow for Copilot
Work in this order:
Briefly inspect the existing implementation and identify the exact relevant functions/files.
Implement Phase A: analysis.
Run relevant tests and the full existing suite.
Implement Phase B: clustering/deduplication.
Run relevant tests and the full suite.
Implement Phase C: opportunity scoring.
Run relevant tests and the full suite.
Implement Phase D: all source adapters, including verified Apify integrations.
Run relevant tests and bounded live integrations.
Implement Phase E: end-to-end orchestration.
Run the complete pipeline on a bounded representative dataset.
Implement Phase F: operator-facing API improvements.
Implement Phase G: UI.
Implement Phase H: production readiness and GCP deployment.
Implement the benchmark suite and report, beginning early enough that it can evaluate each completed stage.
Do not stop after generating a plan. Start modifying the code in the current workspace.
Do not rewrite files that already satisfy the requirements. Prefer small, coherent patches. Do not silently change public API contracts.
If an external credential, account permission, provider quota, or Actor schema blocks live integration, implement the adapter and deterministic tests, document the blocker, and continue with the remaining work.
Required final response after each implementation batch
Report:
Files changed.
Functionality implemented.
Existing behavior preserved.
Commands/tests executed and actual results.
Remaining blockers.
Exact next implementation task.
Update a PROJECT_CHECKLIST.md in the repository with statuses:
DONE — verified by implementation and appropriate tests/integration.
IN PROGRESS — implemented but not fully validated.
BLOCKED — requires an external prerequisite.
NOT STARTED — not implemented.
Do not mark a phase complete merely because files or endpoints exist.
The immediate objective is a functioning end-to-end opportunity discovery pipeline across the existing sources, with Reddit and X integrations added using verified Apify contracts. Once that is working, improve ranking quality and the operator experience iteratively.

