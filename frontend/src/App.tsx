import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  api,
  type Opportunity,
  type OpportunityPage,
  type PipelineRunDetail,
  type PipelineRunList,
  type PipelineRunSummary,
  type PipelineStatus,
  type SourceYield,
} from "./api";

type Page = "discover" | "opportunities" | "sources-runs" | "opportunity-detail";

type DashboardData = {
  health: { status: string; service: string; version: string } | null;
  opportunities: OpportunityPage | null;
  status: PipelineStatus | null;
  runs: PipelineRunList | null;
  sourceYield: SourceYield | null;
  errors: string[];
};

const emptyData: DashboardData = {
  health: null,
  opportunities: null,
  status: null,
  runs: null,
  sourceYield: null,
  errors: [],
};

const categories = [
  "Business Operations",
  "Sales & Marketing",
  "Finance & Accounting",
  "Customer Support",
  "Human Resources",
  "Productivity",
  "Software & IT",
  "Healthcare",
  "Education",
  "Other",
];

const sourceNames: Record<string, string> = {
  hacker_news: "Hacker News",
  stack_exchange: "Stack Exchange",
  reddit: "Reddit",
  x: "X",
};

function readRoute(): { page: Page; id?: number; runId?: string } {
  const [path, query = ""] = window.location.hash.replace(/^#\/?/, "").split("?");
  if (path.startsWith("opportunities/")) {
    const id = Number(path.slice("opportunities/".length));
    return Number.isInteger(id) && id > 0
      ? { page: "opportunity-detail", id }
      : { page: "opportunities" };
  }
  if (path === "opportunities") return { page: "opportunities" };
  if (path === "sources-runs") {
    const runId = new URLSearchParams(query).get("run_id") ?? undefined;
    return { page: "sources-runs", runId };
  }
  return { page: "discover" };
}

function navigate(path: string) {
  window.location.hash = `/${path}`;
}

function dateTime(value: string | null | undefined): string {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? "Not recorded"
    : parsed.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function scoreLabel(score: number | null | undefined): string {
  return typeof score === "number" && Number.isFinite(score)
    ? `${Math.round(score)}`
    : "Not scored";
}

function StatusPill({ value }: { value: string | null | undefined }) {
  const status = value || "unknown";
  const style =
    status === "completed" || status === "likely"
      ? "positive"
      : status === "partial" || status === "running" || status === "unclear"
        ? "caution"
        : status === "failed" || status === "unlikely"
          ? "negative"
          : "neutral";
  return <span className={`pill pill-${style}`}>{status.replaceAll("_", " ")}</span>;
}

function Sidebar({ page }: { page: Page }) {
  const links: Array<{ route: string; label: string; page: Page }> = [
    { route: "discover", label: "Discover", page: "discover" },
    { route: "opportunities", label: "Opportunities", page: "opportunities" },
    { route: "sources-runs", label: "Sources & runs", page: "sources-runs" },
  ];
  return (
    <aside className="sidebar">
      <a className="brand" href="#/discover" aria-label="Fieldnotes home">
        <span className="brand-mark" aria-hidden="true">f</span>
        <span>fieldnotes</span>
      </a>
      <p className="sidebar-label">WORKSPACE</p>
      <nav aria-label="Main navigation">
        {links.map((item) => (
          <a
            key={item.route}
            href={`#/${item.route}`}
            className={`nav-link ${
              page === item.page ||
              (page === "opportunity-detail" && item.page === "opportunities")
                ? "active"
                : ""
            }`}
            aria-current={
              page === item.page ||
              (page === "opportunity-detail" && item.page === "opportunities")
                ? "page"
                : undefined
            }
          >
            <span className={`nav-dot dot-${item.page}`} aria-hidden="true" />
            {item.label}
          </a>
        ))}
      </nav>
      <div className="sidebar-note">
        <span className="eyebrow">EVIDENCE FIRST</span>
        <p>Signals are candidates for investigation, not proof of demand.</p>
      </div>
      <div className="sidebar-footer">Local research workspace</div>
    </aside>
  );
}

function LoadingBlock({ label = "Loading workspace…" }: { label?: string }) {
  return (
    <div className="loading-state" role="status">
      <span className="loading-mark" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

function EmptyState({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon" aria-hidden="true">—</span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}

function Metric({
  label,
  value,
  note,
}: {
  label: string;
  value: string | number;
  note: string;
}) {
  return (
    <div className="metric">
      <p className="eyebrow">{label}</p>
      <strong>{value}</strong>
      <span>{note}</span>
    </div>
  );
}

function OpportunityRows({
  items,
  compact = false,
}: {
  items: Opportunity[];
  compact?: boolean;
}) {
  if (!items.length) {
    return (
      <EmptyState title="No opportunities yet">
        Start a discovery run to collect and analyze source posts. Existing
        database results will appear here; this workspace does not seed sample
        records.
      </EmptyState>
    );
  }

  return (
    <div className="opportunity-list">
      {items.map((item, index) => (
        <a
          className="opportunity-row"
          href={`#/opportunities/${item.cluster_id}`}
          key={item.cluster_id}
        >
          <span className="row-rank">{String(index + 1).padStart(2, "0")}</span>
          <span className="row-main">
            <strong>{item.title}</strong>
            <span className="row-meta">
              {item.category ?? "Category unavailable"}
              <span aria-hidden="true">·</span>
              {item.unique_evidence_count} unique evidence
              {item.unique_evidence_count === 1 ? "" : "s"}
            </span>
          </span>
          {!compact && (
            <span className="row-buildability">
              <StatusPill value={item.software_buildability} />
            </span>
          )}
          <span className="row-score">
            <strong>{scoreLabel(item.score)}</strong>
            <small>{item.score === null ? "score" : "score / 100"}</small>
          </span>
          <span className="row-arrow" aria-hidden="true">↗</span>
        </a>
      ))}
    </div>
  );
}

function RunStatusLine({ run }: { run: PipelineRunSummary }) {
  return (
    <a
      className="run-line"
      href={`#/sources-runs?run_id=${encodeURIComponent(run.run_id)}`}
    >
      <span className="run-led" aria-hidden="true" />
      <span className="run-line-main">
        <strong>{run.run_id.slice(0, 8)}</strong>
        <span>{run.requested_sources.map((source) => sourceNames[source] ?? source).join(", ") || "No source details"}</span>
      </span>
      <span className="run-time">{dateTime(run.started_at)}</span>
      <StatusPill value={run.status} />
    </a>
  );
}

function DiscoverPage({
  data,
  onRefresh,
}: {
  data: DashboardData;
  onRefresh: () => void;
}) {
  const latestRun = data.runs?.items[0];
  const opportunityItems = data.opportunities?.items.slice(0, 5) ?? [];
  const scoredCount = data.opportunities?.items.filter(
    (item) => typeof item.score === "number",
  ).length;
  return (
    <>
      <header className="page-heading">
        <div>
          <p className="eyebrow">PROBLEM DISCOVERY / OVERVIEW</p>
          <h1>Find the friction<br />worth fixing.</h1>
          <p className="page-intro">
            Explore real-world problem reports, keep their source evidence
            close, and treat every score as a starting point for research.
          </p>
        </div>
        <button className="button button-primary" onClick={() => navigate("sources-runs")}>
          <span aria-hidden="true">＋</span> Start discovery
        </button>
      </header>

      {data.errors.length > 0 && (
        <div className="notice notice-warning" role="status">
          <strong>Some workspace data is unavailable.</strong>
          <span>{data.errors.join(" ")}</span>
          <button className="text-button" onClick={onRefresh}>Try again</button>
        </div>
      )}

      <section className="metric-grid" aria-label="Workspace summary">
        <Metric
          label="OPPORTUNITIES"
          value={data.opportunities ? data.opportunities.total : "—"}
          note={data.opportunities ? "From the current database" : "Unavailable"}
        />
        <Metric
          label="SCORED IN VIEW"
          value={scoredCount ?? "—"}
          note={data.opportunities ? "Of up to 100 returned results" : "Unavailable"}
        />
        <Metric
          label="RECENT RUNS"
          value={data.runs ? data.runs.total : "—"}
          note={data.runs ? "Persisted pipeline history" : "Unavailable"}
        />
        <Metric
          label="API"
          value={data.health?.status === "ok" ? "Connected" : "Unavailable"}
          note={data.health ? `Version ${data.health.version}` : "Backend health not confirmed"}
        />
      </section>

      <section className="overview-grid">
        <div className="surface ranked-surface">
          <div className="section-heading">
            <div>
              <p className="eyebrow">CURRENT LANDSCAPE</p>
              <h2>Ranked candidates</h2>
            </div>
            <a className="quiet-link" href="#/opportunities">View all <span aria-hidden="true">→</span></a>
          </div>
          {data.opportunities ? (
            <OpportunityRows items={opportunityItems} compact />
          ) : (
            <Unavailable label="Opportunity results could not be loaded." />
          )}
        </div>

        <div className="overview-aside">
          <section className="surface latest-run">
            <div className="section-heading">
              <div>
                <p className="eyebrow">PIPELINE</p>
                <h2>Latest run</h2>
              </div>
              {data.status && (
                <span className={`lock-indicator ${data.status.run_lock_held ? "is-held" : ""}`}>
                  <span />{data.status.run_lock_held ? "In progress" : "Idle"}
                </span>
              )}
            </div>
            {latestRun ? (
              <div className="latest-run-body">
                <RunStatusLine run={latestRun} />
                <p className="subtle-copy">Started {dateTime(latestRun.started_at)}</p>
              </div>
            ) : data.runs ? (
              <EmptyState title="No runs recorded">
                A discovery run only starts when you choose a source and click
                Start run.
              </EmptyState>
            ) : (
              <Unavailable label="Run history could not be loaded." />
            )}
            <a className="quiet-link bottom-link" href="#/sources-runs">Open sources & runs <span aria-hidden="true">→</span></a>
          </section>

          <section className="source-note">
            <p className="eyebrow">SUPPORTED CONNECTORS</p>
            <div className="source-chip-list">
              {Object.values(sourceNames).map((name) => <span key={name}>{name}</span>)}
            </div>
            <p>Connector support is not a live health check. Provider readiness is not exposed by the API.</p>
          </section>
        </div>
      </section>
    </>
  );
}

function Unavailable({ label }: { label: string }) {
  return (
    <div className="inline-unavailable" role="status">
      <span aria-hidden="true">!</span>{label}
    </div>
  );
}

function OpportunitiesPage({
  data,
  search,
  setSearch,
}: {
  data: DashboardData;
  search: string;
  setSearch: (value: string) => void;
}) {
  const [category, setCategory] = useState("");
  const [evidence, setEvidence] = useState("");
  const [sort, setSort] = useState<"score" | "title" | "evidence_level">("score");
  const [page, setPage] = useState(0);
  const pageSize = 20;
  const items = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return [...(data.opportunities?.items ?? [])]
      .filter((item) => !category || item.category === category)
      .filter((item) => !evidence || item.evidence_level === evidence)
      .filter((item) =>
        !query ||
        item.title.toLocaleLowerCase().includes(query) ||
        (item.category ?? "").toLocaleLowerCase().includes(query),
      )
      .sort((left, right) => {
        if (sort === "title") return left.title.localeCompare(right.title);
        if (sort === "evidence_level") return left.evidence_level.localeCompare(right.evidence_level);
        if (left.score === null) return right.score === null ? left.cluster_id - right.cluster_id : 1;
        if (right.score === null) return -1;
        return right.score - left.score || left.cluster_id - right.cluster_id;
      });
  }, [category, data.opportunities, evidence, search, sort]);
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const visibleItems = items.slice(page * pageSize, (page + 1) * pageSize);
  const evidenceLevels = [...new Set((data.opportunities?.items ?? []).map((item) => item.evidence_level))].sort();

  function updateFilter<T>(setter: (value: T) => void, value: T) {
    setter(value);
    setPage(0);
  }

  return (
    <>
      <header className="page-heading page-heading-small">
        <div>
          <p className="eyebrow">RESEARCH / OPPORTUNITIES</p>
          <h1>Opportunity explorer</h1>
          <p className="page-intro">Compare evidence-backed problem candidates. Scores are heuristics, not validation.</p>
        </div>
        <span className="result-count">{data.opportunities ? `${items.length} shown · ${data.opportunities.total} total` : "Results unavailable"}</span>
      </header>
      <section className="surface explorer">
        <div className="filter-bar">
          <label className="search-field">
            <span aria-hidden="true">⌕</span>
            <span className="visually-hidden">Search title and category</span>
            <input
              value={search}
              onChange={(event) => updateFilter(setSearch, event.target.value)}
              placeholder="Search problems or categories"
            />
          </label>
          <label className="select-field">
            <span className="visually-hidden">Filter by category</span>
            <select value={category} onChange={(event) => updateFilter(setCategory, event.target.value)}>
              <option value="">All categories</option>
              {categories.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label className="select-field">
            <span className="visually-hidden">Filter by evidence level</span>
            <select value={evidence} onChange={(event) => updateFilter(setEvidence, event.target.value)}>
              <option value="">All evidence levels</option>
              {evidenceLevels.map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}
            </select>
          </label>
          <label className="select-field">
            <span className="visually-hidden">Sort opportunities</span>
            <select value={sort} onChange={(event) => updateFilter(setSort, event.target.value as typeof sort)}>
              <option value="score">Score: high to low</option>
              <option value="title">Title: A to Z</option>
              <option value="evidence_level">Evidence level</option>
            </select>
          </label>
        </div>
        {!data.opportunities ? (
          <Unavailable label="Could not load opportunities. Check the API and retry from Discover." />
        ) : visibleItems.length ? (
          <>
            <OpportunityRows items={visibleItems} />
            <div className="pagination">
              <span>Showing {page * pageSize + 1}–{Math.min((page + 1) * pageSize, items.length)} of {items.length} loaded opportunities</span>
              <div>
                <button className="button button-small" onClick={() => setPage((value) => Math.max(0, value - 1))} disabled={page === 0}>Previous</button>
                <span className="page-number">{page + 1} / {pageCount}</span>
                <button className="button button-small" onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))} disabled={page >= pageCount - 1}>Next</button>
              </div>
            </div>
          </>
        ) : (
          <EmptyState title={data.opportunities.items.length ? "No matches for these filters" : "No opportunities found"}>
            {data.opportunities.items.length
              ? "Adjust the search or filters to see other loaded candidates."
              : "Run discovery to add real source evidence. No sample opportunities are inserted."}
          </EmptyState>
        )}
      </section>
    </>
  );
}

function OpportunityDetailPage({ id }: { id: number }) {
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof api.cluster>> | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    api.cluster(id).then((value) => {
      if (active) setDetail(value);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "Could not load opportunity detail.");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [id]);

  if (loading) return <LoadingBlock label="Loading evidence…" />;
  if (error || !detail) {
    return (
      <>
        <a className="back-link" href="#/opportunities">← Back to opportunities</a>
        <div className="notice notice-error" role="alert">{error || "Opportunity detail is unavailable."}</div>
      </>
    );
  }

  const summary = detail.opportunity_summary;
  const evidence = detail.evidence_provenance ?? [];
  return (
    <>
      <a className="back-link" href="#/opportunities">← Back to opportunities</a>
      <header className="detail-heading">
        <div>
          <p className="eyebrow">OPPORTUNITY / CLUSTER {detail.id}</p>
          <h1>{detail.title}</h1>
          <p className="detail-problem">{detail.problem_statement}</p>
          <div className="detail-tags">
            <span>{detail.category ?? "Category unavailable"}</span>
            {detail.industry && <span>{detail.industry}</span>}
            <StatusPill value={summary?.evidence_level ?? "evidence unavailable"} />
          </div>
        </div>
        <div className="detail-score">
          <span className="eyebrow">OPPORTUNITY SCORE</span>
          <strong>{scoreLabel(summary?.score)}</strong>
          <small>{summary?.score === null || !summary ? "Not available" : "Heuristic / 100"}</small>
        </div>
      </header>
      <section className="detail-grid">
        <div className="surface detail-main">
          <div className="section-heading">
            <div><p className="eyebrow">SOURCE MATERIAL</p><h2>Evidence trail</h2></div>
            <span className="muted-count">{evidence.length} attached records</span>
          </div>
          {evidence.length ? (
            <div className="evidence-list">
              {evidence.map((item) => (
                <article className="evidence-item" key={item.evidence_id}>
                  <div className="evidence-topline">
                    <strong>{item.source_name}</strong>
                    <time dateTime={item.submission_created_at}>{dateTime(item.submission_created_at)}</time>
                  </div>
                  <div className="evidence-meta">
                    <span>Submission {item.submission_id}</span>
                    <span>{item.relationship_type.replaceAll("_", " ")}</span>
                    <span>{item.scoring_status.replaceAll("_", " ")}</span>
                  </div>
                  {item.source_url ? (
                    <a className="source-url" href={item.source_url} target="_blank" rel="noreferrer">
                      Open original source <span aria-hidden="true">↗</span>
                    </a>
                  ) : <span className="muted-copy">Original source URL not recorded</span>}
                  {item.source_observations.length > 0 && (
                    <div className="observation-list">
                      {item.source_observations.map((observation, index) => (
                        <div key={`${observation.source_name}-${observation.native_id ?? index}`}>
                          <span>{observation.source_name}{observation.community ? ` · ${observation.community}` : ""}</span>
                          <span>{observation.native_id ? `ID ${observation.native_id} · ` : ""}{observation.timestamp_origin.replaceAll("_", " ")}</span>
                          {observation.source_url && <a href={observation.source_url} target="_blank" rel="noreferrer">Source ↗</a>}
                        </div>
                      ))}
                    </div>
                  )}
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="No provenance available">
              The API returned no evidence provenance for this cluster.
            </EmptyState>
          )}
        </div>
        <aside className="detail-aside">
          <section className="surface score-explanation">
            <p className="eyebrow">SCORING & LIMITS</p>
            <h2>What the evidence says</h2>
            <dl className="detail-facts">
              <div><dt>Buildability</dt><dd><StatusPill value={summary?.software_buildability ?? "unavailable"} /></dd></div>
              <div><dt>Unique evidence</dt><dd>{summary?.unique_evidence_count ?? "Not available"}</dd></div>
              <div><dt>Sources</dt><dd>{summary?.unique_source_platform_count ?? "Not available"}</dd></div>
              <div><dt>Verified independent</dt><dd>{detail.verified_independent_evidence_count}</dd></div>
              <div><dt>Eligibility</dt><dd>{summary?.eligibility?.replaceAll("_", " ") ?? "Not available"}</dd></div>
            </dl>
            {summary?.reasons?.length ? (
              <div className="explanation-list"><h3>Score reasons</h3><ul>{summary.reasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}</ul></div>
            ) : <p className="muted-copy">No score explanation was returned.</p>}
            {summary?.limitations?.length ? (
              <div className="explanation-list limitations"><h3>Limitations</h3><ul>{summary.limitations.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></div>
            ) : null}
            <p className="honesty-note">A score is a prioritization heuristic. It does not validate demand or prove independent customers.</p>
          </section>
        </aside>
      </section>
    </>
  );
}

function SourcesRunsPage({
  data,
  onRunComplete,
  routeRunId,
}: {
  data: DashboardData;
  onRunComplete: (result: Record<string, unknown>) => void;
  routeRunId?: string;
}) {
  const [runId, setRunId] = useState<string | undefined>(readRoute().runId);
  const [selectedSources, setSelectedSources] = useState<string[]>(["hacker_news", "reddit"]);
  const [limits, setLimits] = useState({ hackerNews: 10, stackExchange: 10, reddit: 5, analysis: 10, clustering: 10 });
  const [selectedRun, setSelectedRun] = useState<PipelineRunDetail | null>(null);
  const [runLoading, setRunLoading] = useState(false);
  const [runError, setRunError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [confirmedResult, setConfirmedResult] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (routeRunId && routeRunId !== runId) setRunId(routeRunId);
  }, [routeRunId, runId]);

  useEffect(() => {
    if (!runId) {
      setSelectedRun(null);
      setRunError("");
      return;
    }
    let active = true;
    setRunLoading(true);
    setRunError("");
    api.pipelineRun(runId).then((result) => {
      if (active) setSelectedRun(result);
    }).catch((reason: unknown) => {
      if (active) setRunError(reason instanceof Error ? reason.message : "Could not load run details.");
    }).finally(() => {
      if (active) setRunLoading(false);
    });
    return () => { active = false; };
  }, [runId]);

  function selectRun(id: string, justStarted = false) {
    setRunId(id);
    setSubmitError("");
    if (!justStarted) setConfirmedResult(null);
    window.location.hash = `/sources-runs?run_id=${encodeURIComponent(id)}`;
  }

  function toggleSource(source: string) {
    setSelectedSources((current) =>
      current.includes(source)
        ? current.filter((item) => item !== source)
        : [...current, source],
    );
  }

  async function submitRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedSources.length || submitting) return;
    setSubmitting(true);
    setSubmitError("");
    setConfirmedResult(null);
    setSelectedRun(null);
    setRunId(undefined);
    try {
      const result = await api.startPipeline({
        sources: selectedSources,
        hacker_news_limit: limits.hackerNews,
        stack_exchange_limit: limits.stackExchange,
        stack_exchange_site: "stackoverflow",
        reddit_limit: limits.reddit,
        x_limit: 50,
        analysis_limit: limits.analysis,
        clustering_limit: limits.clustering,
      });
      setConfirmedResult(result);
      onRunComplete(result);
      if (typeof result.run_id === "string") selectRun(result.run_id, true);
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : "The run could not be started.");
    } finally {
      setSubmitting(false);
    }
  }

  const yields = data.sourceYield?.communities ?? [];
  return (
    <>
      <header className="page-heading page-heading-small">
        <div>
          <p className="eyebrow">COLLECTION / PIPELINE</p>
          <h1>Sources & runs</h1>
          <p className="page-intro">Choose connectors explicitly. Collection can use paid or rate-limited providers.</p>
        </div>
      </header>

      <div className="sources-layout">
        <div className="sources-main">
          <section className="surface source-health">
            <div className="section-heading">
              <div><p className="eyebrow">CONNECTORS</p><h2>Available integrations</h2></div>
              <span className="muted-copy">Availability is based on backend support, not live provider health.</span>
            </div>
            <div className="connector-grid">
              {Object.entries(sourceNames).map(([key, label]) => {
                const matched = yields.filter((row) =>
                  key === "stack_exchange"
                    ? row.source_name.startsWith("Stack Exchange")
                    : row.source_name.toLowerCase() === label.toLowerCase(),
                );
                const collected = matched.reduce((total, row) => total + row.collected, 0);
                return (
                  <article className="connector" key={key}>
                    <div className="connector-mark" aria-hidden="true">{label.slice(0, 1)}</div>
                    <div className="connector-copy">
                      <strong>{label}</strong>
                      <span>{matched.length ? `${collected} collected records in database` : data.sourceYield ? "No collected records reported" : "Usage data unavailable"}</span>
                    </div>
                    <span className="connector-state">Supported</span>
                  </article>
                );
              })}
            </div>
            {data.sourceYield === null && <Unavailable label="Source yield data could not be loaded." />}
          </section>

          <section className="surface discovery-form-surface">
            <div className="section-heading">
              <div><p className="eyebrow">EXPLICIT ACTION</p><h2>Start a discovery run</h2></div>
              <span className="caution-label">May call external providers</span>
            </div>
            <p className="form-intro">Only new records collected in this run are analyzed and clustered. Existing database results are not automatically reprocessed.</p>
            <form onSubmit={submitRun}>
              <fieldset className="source-checks">
                <legend>Sources to collect</legend>
                {Object.entries(sourceNames).map(([key, label]) => (
                  <label className="source-check" key={key}>
                    <input type="checkbox" checked={selectedSources.includes(key)} onChange={() => toggleSource(key)} />
                    <span>{label}</span>
                    {key === "x" && <small>Fixed 50-item request; verify pricing/access first</small>}
                  </label>
                ))}
              </fieldset>
              <div className="limit-grid">
                {selectedSources.includes("hacker_news") && (
                  <label>Hacker News limit<input type="number" min="1" max="100" value={limits.hackerNews} onChange={(event) => setLimits({ ...limits, hackerNews: Number(event.target.value) })} /></label>
                )}
                {selectedSources.includes("reddit") && (
                  <label>Reddit limit<input type="number" min="1" max="25" value={limits.reddit} onChange={(event) => setLimits({ ...limits, reddit: Number(event.target.value) })} /></label>
                )}
                {selectedSources.includes("stack_exchange") && (
                  <label>Stack Exchange limit<input type="number" min="1" max="100" value={limits.stackExchange} onChange={(event) => setLimits({ ...limits, stackExchange: Number(event.target.value) })} /></label>
                )}
                <label>Analysis limit<input type="number" min="1" max="30" value={limits.analysis} onChange={(event) => setLimits({ ...limits, analysis: Number(event.target.value) })} /></label>
                <label>Clustering limit<input type="number" min="1" max="30" value={limits.clustering} onChange={(event) => setLimits({ ...limits, clustering: Number(event.target.value) })} /></label>
              </div>
              {submitError && <div className="notice notice-error" role="alert">{submitError}</div>}
              <div className="form-footer">
                <p>Submitting may incur provider costs. No collection happens until you press this button.</p>
                <button className="button button-primary" type="submit" disabled={!selectedSources.length || submitting}>
                  {submitting ? <><span className="button-spinner" /> Starting…</> : "Start run"}
                </button>
              </div>
            </form>
          </section>

          <section className="surface run-history">
            <div className="section-heading"><div><p className="eyebrow">HISTORY</p><h2>Recent runs</h2></div><span className="muted-count">{data.runs ? `${data.runs.total} total` : "Unavailable"}</span></div>
            {!data.runs ? <Unavailable label="Run history could not be loaded." /> :
              data.runs.items.length ? (
                <div className="run-history-list">
                  {data.runs.items.map((run) => (
                    <button className={`run-history-item ${run.run_id === runId ? "selected" : ""}`} key={run.run_id} onClick={() => selectRun(run.run_id)}>
                      <span className="history-id">{run.run_id.slice(0, 8)}</span>
                      <span className="history-sources">{run.requested_sources.map((source) => sourceNames[source] ?? source).join(", ") || "Sources unavailable"}</span>
                      <span className="history-date">{dateTime(run.started_at)}</span>
                      <StatusPill value={run.status} />
                    </button>
                  ))}
                </div>
              ) : <EmptyState title="No discovery runs yet">Choose one or more sources above and start a run when you are ready.</EmptyState>}
          </section>
          {confirmedResult && (
            <section className="surface confirmed-run" aria-live="polite">
              <div className="section-heading">
                <div><p className="eyebrow">BACKEND CONFIRMATION</p><h2>Run response received</h2></div>
                {typeof confirmedResult.status === "string" && <StatusPill value={confirmedResult.status} />}
              </div>
              <p>Run ID: <code>{typeof confirmedResult.run_id === "string" ? confirmedResult.run_id : "Not returned"}</code></p>
              <p>{Array.isArray(confirmedResult.collected_submission_ids) ? confirmedResult.collected_submission_ids.length : 0} submission IDs were associated with this run.</p>
              {Array.isArray(confirmedResult.failures) && confirmedResult.failures.length > 0 && <p className="confirmed-failures">{confirmedResult.failures.length} stage/source failure(s) reported. Review the persisted run details.</p>}
            </section>
          )}
        </div>

        <aside className="run-detail-column">
          <section className="surface run-detail">
            <div className="section-heading"><div><p className="eyebrow">RUN INSPECTOR</p><h2>{runId ? "Run detail" : "Select a run"}</h2></div></div>
            {submitting ? <LoadingBlock label="Waiting for the backend run result…" /> :
              runLoading ? <LoadingBlock label="Loading persisted run details…" /> :
              submitError ? <Unavailable label="Run was not confirmed. Resolve the error before retrying." /> :
              runError ? <div className="notice notice-error" role="alert">{runError}</div> :
              selectedRun ? <RunDetails run={selectedRun} /> :
              <EmptyState title="No run selected">Select a history entry to inspect its sources, results, and status.</EmptyState>}
          </section>
        </aside>
      </div>
    </>
  );
}

function RunDetails({ run }: { run: PipelineRunDetail }) {
  const stageCounts = run.stage_counts;
  const counts = Object.entries(stageCounts).filter(([, value]) => value && typeof value === "object") as Array<[string, Record<string, unknown>]>;
  return (
    <div className="run-detail-content">
      <div className="run-detail-status"><StatusPill value={run.status} /><span>{run.run_id}</span></div>
      <dl className="run-facts">
        <div><dt>Started</dt><dd>{dateTime(run.started_at)}</dd></div>
        <div><dt>Completed</dt><dd>{dateTime(run.completed_at)}</dd></div>
        <div><dt>Collected IDs</dt><dd>{run.collected_submission_ids.length}</dd></div>
        <div><dt>Processed records</dt><dd>{run.submissions.length}</dd></div>
      </dl>
      {counts.map(([stage, rows]) => (
        <div className="stage-summary" key={stage}>
          <h3>{stage}</h3>
          <div>{Object.entries(rows).map(([key, value]) => <span key={key}>{key.replaceAll("_", " ")} <strong>{String(value)}</strong></span>)}</div>
        </div>
      ))}
      {run.failures.length > 0 && (
        <div className="notice notice-warning">
          <strong>Partial or failed work</strong>
          <ul>{run.failures.map((failure, index) => <li key={`${failure.code ?? "failure"}-${index}`}>{[failure.stage, failure.source, failure.code].filter(Boolean).join(" · ") || "Failure details unavailable"}</li>)}</ul>
        </div>
      )}
      <h3 className="run-section-title">Collected submissions</h3>
      {run.submissions.length ? (
        <div className="run-submission-list">{run.submissions.map((item) => (
          <div key={item.submission_id} className="run-submission">
            <strong>#{item.submission_id} · {item.source_name}</strong>
            <span>Analysis: {item.analysis_status.replaceAll("_", " ")}</span>
            <span>Clustering: {item.clustering_status.replaceAll("_", " ")}</span>
            {item.source_url && <a href={item.source_url} target="_blank" rel="noreferrer">Original source ↗</a>}
          </div>
        ))}</div>
      ) : <p className="muted-copy">No submission IDs were associated with this run.</p>}
      <h3 className="run-section-title">Opportunity clusters</h3>
      {run.opportunity_clusters.length ? run.opportunity_clusters.map((cluster) => (
        <a className="run-cluster" href={`#/opportunities/${cluster.cluster_id}`} key={cluster.cluster_id}>
          <strong>{cluster.title}</strong>
          <span>{cluster.run_evidence_count} run records · {cluster.total_attached_evidence_count} total cluster records</span>
          {cluster.contains_evidence_outside_run && <em>Includes evidence from outside this run</em>}
        </a>
      )) : <p className="muted-copy">This run did not return an opportunity cluster.</p>}
    </div>
  );
}

export default function App() {
  const [route, setRoute] = useState(readRoute);
  const [data, setData] = useState<DashboardData>(emptyData);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    const results = await Promise.allSettled([
      api.health(),
      api.opportunities(),
      api.pipelineStatus(),
      api.pipelineRuns(),
      api.sourceYield(),
    ]);
    const errors: string[] = [];
    const take = <T,>(index: number, label: string): T | null => {
      const result = results[index];
      if (result.status === "fulfilled") return result.value as T;
      errors.push(`${label}: ${result.reason instanceof Error ? result.reason.message : "request failed"}`);
      return null;
    };
    setData({
      health: take<NonNullable<DashboardData["health"]>>(0, "Health"),
      opportunities: take<OpportunityPage>(1, "Opportunities"),
      status: take<PipelineStatus>(2, "Pipeline status"),
      runs: take<PipelineRunList>(3, "Run history"),
      sourceYield: take<SourceYield>(4, "Source usage"),
      errors,
    });
    setLoading(false);
  }, []);

  useEffect(() => {
    const onHashChange = () => setRoute(readRoute());
    window.addEventListener("hashchange", onHashChange);
    void refresh();
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [refresh]);

  const latestRun = data.runs?.items[0];
  const title = route.page === "opportunities" || route.page === "opportunity-detail"
    ? "Opportunities"
    : route.page === "sources-runs" ? "Sources & runs" : "Discover";

  if (loading && !data.health && !data.opportunities && !data.runs) {
    return (
      <div className="app-shell">
        <Sidebar page={route.page} />
        <main className="main-area"><LoadingBlock /></main>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <Sidebar page={route.page} />
      <main className="main-area">
        <div className="topbar">
          <div className="breadcrumb"><span>Workspace</span><span aria-hidden="true">/</span><strong>{title}</strong></div>
          <div className="topbar-right">
            <span className="api-state"><i className={data.health?.status === "ok" ? "online" : ""} />{data.health?.status === "ok" ? "Local API connected" : "API unavailable"}</span>
            <button className="icon-button" onClick={() => void refresh()} aria-label="Refresh workspace" title="Refresh workspace">↻</button>
          </div>
        </div>
        <div className="content-area">
          {route.page === "discover" && <DiscoverPage data={data} onRefresh={() => void refresh()} />}
          <div hidden={route.page !== "opportunities"}>
            <OpportunitiesPage data={data} search={search} setSearch={setSearch} />
          </div>
          {route.page === "opportunity-detail" && route.id && <OpportunityDetailPage id={route.id} />}
          <div hidden={route.page !== "sources-runs"}>
            <SourcesRunsPage
              data={data}
              routeRunId={route.runId}
              onRunComplete={() => {
                void refresh();
              }}
            />
          </div>
          {latestRun && route.page === "discover" && <span className="visually-hidden">Latest pipeline status: {latestRun.status}</span>}
        </div>
        <footer className="app-footer">
          <span>FIELDNOTES <i>·</i> LOCAL RESEARCH WORKSPACE</span>
          <span>{data.health ? `API v${data.health.version}` : "Backend version unavailable"}</span>
        </footer>
      </main>
    </div>
  );
}
