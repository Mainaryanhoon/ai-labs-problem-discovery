# Operator API foundation

Phase F adds backend review surfaces for a future founder-facing frontend. The
API remains the source of truth; no frontend is included.

## Opportunity discovery

- `GET /api/opportunities` returns a page with `items`, `total`, `limit`, and
  `offset`. It supports exact `category`, `status`, and `evidence_level`
  filters, `min_score`/`max_score`, and deterministic sorting by `score`,
  `title`, `evidence_level`, `created_at`, or `updated_at`.
- Score sorting calculates a summary for every candidate cluster matching the
  category/status filters before applying evidence-level/score filters,
  sorting, and pagination. Unscored opportunities sort last; ID breaks ties.
- `GET /api/clusters/{cluster_id}` retains the existing response fields and
  adds raw/unique/duplicate evidence counts and source provenance.
- `GET /api/clusters/{cluster_id}/opportunity` retains its existing scoring
  fields and adds `evidence_provenance` and `duplicate_exclusions`. Duplicate
  explanations identify the excluded submission, the selected canonical
  submission and its source name, original URL, and timestamp, plus whether
  canonical URL and/or exact text caused exclusion.

`created_at` on an evidence provenance item is when it was attached;
`submission_created_at` is the timestamp stored on the submission row.
The additive `source_records` table stores a submission's primary provider ID,
community (where available), timestamp origin, and collection time. The
additive `source_observations` table preserves source-specific URLs, native
IDs, communities, source timestamps, and collection times for each observation,
including duplicates linked to an existing submission. Same-source retries
are idempotent; a cross-source or separate-URL duplicate adds an observation
without creating another analysis submission. Evidence responses include the
original URLs for every observation. Existing records are preserved, and
missing historical IDs or timestamps are not invented.

`GET /api/sources/yield` returns collected, analyzed, genuine, and clustered
counts grouped by source and community. Malformed or incomplete analyses do
not count as analyzed. A genuine rate is `null` when no valid analysis exists.
Manual submissions are not included because they have no collection
provenance record.

Optional protections are disabled by default. Setting `SERVICE_API_KEY`
requires an `X-API-Key` header on every API route except `/health`. Setting
`SUBMISSION_RATE_LIMIT_PER_MINUTE` limits `POST /api/submissions` by client
address. The in-memory limit is per worker process, not global across multiple
workers or hosts; configure trusted proxy handling at deployment rather than
relying on forwarded headers here.

`POST /api/pipeline/run` and `POST /api/ingestion/reddit` accept optional
subreddit names for Reddit listing collection. This changes the actor input
to subreddit `/new/` `startUrls`; the supplied actor documentation lists
subreddit listing URLs as supported, but no live run has verified the
configured Actor/account.
For explicit subreddit lists, the overall Reddit `limit` is divided evenly
across the selected communities (remainder assigned in request order), then
enforced locally. The Actor `maxItems` remains the overall cap; the Actor may
return an uneven mix, so local caps can result in fewer stored posts than the
overall limit. Reddit default search uses the Actor's `searchQueries` input.
Reddit comments and subreddit metadata are ignored; only `itemType: "post"`
records are considered.

X collection uses one broad query and is deliberately fixed at 50 items to
meet the supplied Actor documentation's per-query minimum. `limit` values
other than 50 are rejected. The actor/account eligibility, the exact `sort`
enum, whether this query can actually yield 50 results, and any free-tier
restrictions are not verified. This can incur a paid run; confirm current
pricing and account eligibility before calling the endpoint. A process-local
guard rejects a second simultaneous X run in the same worker; it does not
coordinate multiple API workers/hosts, so use a single worker for the smoke
test and do not start another run until the first has completed.

The collector accepts X records with the documented fields even if the Actor
omits an extra `type` field; an explicit non-`tweet` type is skipped. When
provider output includes explicit repost, reply, or quote metadata, it is
preserved with the pipeline run and passed as context to analysis. Explicit
reposts are retained but deterministically classified as non-genuine evidence;
quote posts remain eligible for review and are never treated as independent.
Missing provider metadata does not prove that a record is original or
first-person evidence. The classifier prompt asks for conservative handling
of support replies, vague frustration, speculation, and quoted content, but
model output is not guaranteed by prompt wording.

The extraction schema restricts AI-produced categories to a canonical set:
`Business Operations`, `Sales & Marketing`, `Finance & Accounting`,
`Customer Support`, `Human Resources`, `Productivity`, `Software & IT`,
`Healthcare`, `Education`, and `Other`. Buildability is classified as
`likely`, `unclear`, or `unlikely`; absent legacy values are treated as
unclear and are not numerically ranked. Filter retrieval by category with
`GET /api/opportunities?category=Business%20Operations`.

The orchestration endpoint requires at least one collection source and will
collect again. Each run persists its UUID, status, timestamps, stage counts,
sanitized failures, and associations to submissions created by successful
collectors. `POST /api/pipeline/run` returns `run_id` and collected submission
IDs per source and at top level. Analysis and clustering are limited to those
IDs; older pending or unclustered records are not consumed by a new run.
Collection adapters currently report newly created records as collected IDs;
records skipped as duplicates are not associated with the run.

Inspect recent runs with `GET /api/pipeline/runs`, then inspect one with
`GET /api/pipeline/runs/{run_id}`. The detail response lists run submissions,
analysis/clustering state, attached opportunity clusters, and review candidate
cluster IDs. Opportunity summaries are still cluster-wide: the run response
shows `run_evidence_count`, `total_attached_evidence_count`, and
`contains_evidence_outside_run` so historical evidence is not presented as
belonging exclusively to the run. Status is `completed` when the selected
work finishes without failures or limit-deferred items, `partial` when there
are stage failures or work remains deferred by a stage limit, and `failed`
when every requested collection source failed.

To process selected, already-persisted records without
recollection, use `POST /api/submissions/{submission_id}/analyze`, then
`POST /api/submissions/{submission_id}/cluster`, then inspect the cluster
through `GET /api/clusters/{cluster_id}/opportunity` or
`GET /api/opportunities`. This targeted path uses the configured Gemini and
embedding providers and can incur their costs; it avoids running analysis on
an unrelated pending backlog.
Original source URLs are returned unchanged. `independent_evidence_count` is retained for API
compatibility and represents recorded flags after duplicate removal;
`recorded_independent_evidence_count` names that meaning explicitly.
`verified_independent_evidence_count` remains zero because no verification
workflow exists.

Scores are prioritization heuristics, not proof of demand, market size,
purchase intent, or business viability. Duplicate posts do not increase
unique evidence or corroboration claims. A `single_source_candidate` is a
candidate for investigation, never a validated opportunity; keep its evidence
level, original source, and limitations visible beside its score.
Only likely-software-buildable clusters with valid supporting evidence receive
a numeric score. Up to 15 transparent growth points use at most three unique
supporting submissions with source timestamps within the preceding seven days.
Collection-time fallbacks and deduplicated observations do not count as
timestamped growth.

## Weekly rankings

`POST /api/rankings/weekly/snapshots` records the eligible top 10 for the
current UTC week. Repeated calls during that week return the immutable
existing snapshot and do not recollect or reanalyze evidence. Snapshots retain
rank, cluster ID/title/category, score, evidence level, score reasons,
limitations, and the timestamped-growth count.

Use `GET /api/rankings/weekly/snapshots?limit=12` for history,
`GET /api/rankings/weekly/snapshots/{YYYY-MM-DD}` for one snapshot, and
`GET /api/rankings/weekly/trends/{cluster_id}?weeks=12` for weekly movement.
Week dates are UTC Mondays. Missing ranks in an adjacent snapshot produce no
rank-change value; movement is not inferred across missing weeks.

Windows CMD examples:

```cmd
curl.exe --fail-with-body "http://127.0.0.1:8000/api/opportunities?category=Business%20Operations&limit=10"
curl.exe --fail-with-body -X POST "http://127.0.0.1:8000/api/rankings/weekly/snapshots"
curl.exe --fail-with-body "http://127.0.0.1:8000/api/rankings/weekly/snapshots?limit=12"
curl.exe --fail-with-body "http://127.0.0.1:8000/api/rankings/weekly/snapshots/2026-10-19"
curl.exe --fail-with-body "http://127.0.0.1:8000/api/rankings/weekly/trends/33?weeks=12"
```

Example response shape (illustrative values, not live results):

```json
{
  "week_start": "2026-10-19",
  "generated_at": "2026-10-20T12:00:00Z",
  "top_k": 10,
  "items": [{
    "rank": 1,
    "cluster_id": 33,
    "title": "Example candidate",
    "category": "Business Operations",
    "score": 64.0,
    "evidence_level": "single_source_candidate",
    "source_timestamped_records_7d": 1,
    "reasons": ["Explainable score component"],
    "limitations": ["Single source; not validated demand"]
  }]
}
```

## Offline evaluation

`python -m app.evaluation` compares JSONL predictions to human labels without
calling providers, accessing the database, or applying thresholds. The
prediction field `prefilter_kept` measures prefilter recall against labelled
genuine records. A deterministic blind sample of rejected text can be written
with `--rejected-sample-output`, `--rejected-sample-size`, and
`--sample-seed`. Synthetic files in `benchmark/` validate the measurement path
only; they do not establish accuracy. See
[the benchmark guide](../benchmark/README.md) for the label schema and workflow.

The health response identifies the running FastAPI application version.
Start locally with
`.venv\Scripts\python.exe -m uvicorn app.main:app --reload`; from a second
Windows CMD window, verify it with
`curl.exe --fail-with-body http://127.0.0.1:8000/health` and inspect the
contract with `curl.exe --fail-with-body http://127.0.0.1:8000/openapi.json`.
Neither verification command calls an external provider.

## Local dashboard

The optional React/TypeScript dashboard runs separately from FastAPI. Start
the backend as described in the repository README, then in another Windows
CMD window:

```cmd
cd /d C:\Users\singh\OneDrive\Desktop\ai-labs-problem-discovery\ai-labs-problem-discovery\frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Its Vite development proxy forwards `/api` and
`/health` to `http://127.0.0.1:8000`; set `VITE_API_PROXY_TARGET` if the local
API uses another origin. On page load it performs read-only
requests for health, opportunities, pipeline status/history, and source yield.
It never starts a run automatically. The explicit run form calls
`POST /api/pipeline/run`; its run inspector uses
`GET /api/pipeline/runs/{run_id}`. Opportunity detail uses
`GET /api/clusters/{cluster_id}`. UI tests mock these calls and do not invoke
providers. If `SERVICE_API_KEY` protection is enabled, the dashboard does not
send a key; use the API in the documented protected deployment/proxy setup
rather than embedding a secret in the browser bundle.

The dashboard shows support for Hacker News, Stack Exchange, Reddit, and X
because the backend implements those collectors. `GET /api/sources/yield`
shows records already represented in the database; it does not indicate
current provider credentials or availability. X runs use the backend's fixed
50-item request and can incur provider costs.

## Review and pipeline status

- `GET /api/review-queue` provides paginated `all`, `analysis`, and
  `clustering` queues for pending/failed analysis and needs-review/failed
  clustering. Error values are safe codes, not stored exception details.
- `GET /api/pipeline/status` reports whether a lock row is held, whether its
  lease expired, whether owner liveness is unknown, and aggregate persisted
  analysis/clustering states. It does not claim that an expired owner stopped.
  Run history is available from `GET /api/pipeline/runs`; per-run stage counts
  and failures are persisted, but live provider health is not inferred.
- `POST /api/submissions/{submission_id}/analyze` refuses to overwrite a
  completed analysis unless called with `force=true`. A retry clears prior
  output before provider work begins, so failed work cannot appear successful.
- `POST /api/submissions/analyze-pending?retry_failed=true` explicitly includes
  failed records in a batch retry. The pipeline retains its existing explicit
  `retry_failed_analysis` control.

## Stale pipeline lock recovery

The pipeline lock's lease is only a liveness hint; lease expiry does not prove
that the owning process has stopped. The API never takes over an existing lock,
even when `recover_interrupted_run=true` is supplied. That field is retained
only for request compatibility.

To clear a stranded lock safely:

1. Stop the API and confirm every worker process that could run the pipeline
   has stopped.
2. Connect to the configured database using the normal database administration
   method without printing credentials or `.env` contents.
3. Clear the singleton lock row:

   ```sql
   UPDATE pipeline_run_locks
   SET owner_token = NULL, started_at = NULL, locked_until = NULL
   WHERE id = 1;
   ```

4. Reset interrupted analysis records:

   ```sql
   UPDATE submissions
   SET analysis_status = 'pending'
   WHERE analysis_status = 'processing';
   ```

5. Restart the API, check `GET /api/pipeline/status`, then submit a new run.

Do not execute this procedure while an API worker may still be active.

## Still outstanding

- Live Hacker News, Stack Exchange, Apify actor/account, and Gemini validation
  remains unverified.
- A PRAW-backed Reddit adapter is a future cost-reduction task; verify API
  access/usage requirements and compare cost and coverage before switching.
- Human-labeled classification, prefilter, clustering, and ranking evaluation
  remains necessary. Unit tests do not establish real-world accuracy or
  calibrated thresholds.
- Production hardening, deployment, multi-host ownership, and scheduled
  snapshot generation remain out of scope; weekly snapshots are
  operator-triggered.
