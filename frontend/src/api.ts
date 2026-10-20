export type PipelineStatus = {
  run_lock_held: boolean;
  lock_lease_expired: boolean;
  owner_liveness_unknown: boolean;
  run_started_at: string | null;
  lease_expires_at: string | null;
  analysis_status_counts: Record<string, number>;
  clustering_status_counts: Record<string, number>;
  run_history_available: true;
};

export type Opportunity = {
  cluster_id: number;
  title: string;
  category: string | null;
  status: string | null;
  created_at: string | null;
  updated_at: string | null;
  score: number | null;
  evidence_level: string;
  software_buildability: string;
  software_buildability_reasons: string[];
  eligibility: string;
  raw_attached_record_count: number;
  analyzed_submission_count: number;
  unique_evidence_count: number;
  eligible_evidence_count: number;
  unique_source_platform_count: number;
  reasons: string[];
  limitations: string[];
  evidence_provenance: EvidenceProvenance[];
};

export type EvidenceProvenance = {
  evidence_id: number;
  submission_id: number;
  source_name: string;
  source_url: string | null;
  created_at: string;
  submission_created_at: string;
  analysis_status: string;
  clustering_status: string | null;
  relationship_type: string;
  recorded_independent: boolean;
  verified_independent: boolean;
  duplicate_of_submission_id: number | null;
  duplicate_reasons: string[];
  scoring_status: string;
  source_observations: Array<{
    source_name: string;
    source_url: string | null;
    native_id: string | null;
    community: string | null;
    source_created_at: string | null;
    timestamp_origin: string;
    collected_at: string;
  }>;
};

export type OpportunityPage = {
  items: Opportunity[];
  total: number;
  limit: number;
  offset: number;
};

export type PipelineRunSummary = {
  run_id: string;
  status: "running" | "completed" | "partial" | "failed";
  requested_sources: string[];
  stage_counts: Record<string, unknown>;
  failures: Array<{ stage?: string; source?: string; code?: string }>;
  started_at: string;
  completed_at: string | null;
  collected_submission_ids: number[];
};

export type PipelineRunList = {
  items: PipelineRunSummary[];
  total: number;
  limit: number;
  offset: number;
};

export type PipelineRunDetail = PipelineRunSummary & {
  submissions: Array<{
    submission_id: number;
    source_name: string;
    source_url: string | null;
    analysis_status: string;
    clustering_status: string;
    cluster_id: number | null;
    source_metadata: Record<string, unknown>;
  }>;
  opportunity_clusters: Array<{
    cluster_id: number;
    title: string;
    run_submission_ids: number[];
    run_evidence_count: number;
    total_attached_evidence_count: number;
    contains_evidence_outside_run: boolean;
    opportunity_summary: Opportunity;
  }>;
  review_candidate_cluster_ids: number[];
};

export type SourceYield = {
  communities: Array<{
    source_name: string;
    community: string | null;
    collected: number;
    analyzed: number;
    genuine: number;
    clustered: number;
    genuine_rate: number | null;
  }>;
};

export type PipelineRequest = {
  sources: string[];
  hacker_news_limit: number;
  stack_exchange_limit: number;
  stack_exchange_site: string;
  reddit_limit: number;
  x_limit: 50;
  analysis_limit: number;
  clustering_limit: number;
};

const apiBase = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new Error("Could not reach the local API. Check that the backend is running.");
  }

  if (!response.ok) {
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      detail = "";
    }
    throw new Error(detail || `The API returned HTTP ${response.status}.`);
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new Error("The API returned an invalid JSON response.");
  }
  if (!isRecord(body)) {
    throw new Error("The API returned an unexpected response shape.");
  }
  return body as T;
}

export const api = {
  health: () => request<{ status: string; service: string; version: string }>("/health"),
  opportunities: async () => {
    const result = await request<unknown>(
      "/api/opportunities?limit=100&offset=0&sort_by=score&sort_order=desc",
    );
    if (
      !isRecord(result) ||
      !Array.isArray(result.items) ||
      typeof result.total !== "number"
    ) {
      throw new Error("The opportunities endpoint returned an invalid response.");
    }
    return result as OpportunityPage;
  },
  pipelineStatus: () => request<PipelineStatus>("/api/pipeline/status"),
  pipelineRuns: () => request<PipelineRunList>("/api/pipeline/runs?limit=8&offset=0"),
  pipelineRun: (runId: string) =>
    request<PipelineRunDetail>(`/api/pipeline/runs/${encodeURIComponent(runId)}`),
  sourceYield: () => request<SourceYield>("/api/sources/yield"),
  cluster: (clusterId: number) =>
    request<{
      id: number;
      title: string;
      problem_statement: string;
      affected_user: string | null;
      category: string | null;
      industry: string | null;
      status: string;
      evidence_count: number;
      unique_evidence_count: number;
      duplicate_evidence_count: number;
      verified_independent_evidence_count: number;
      evidence_provenance: EvidenceProvenance[];
      opportunity_summary: Opportunity | null;
    }>(`/api/clusters/${clusterId}`),
  startPipeline: (payload: PipelineRequest) =>
    request<Record<string, unknown>>("/api/pipeline/run", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
