import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockedApi = vi.hoisted(() => ({
  health: vi.fn(),
  opportunities: vi.fn(),
  pipelineStatus: vi.fn(),
  pipelineRuns: vi.fn(),
  pipelineRun: vi.fn(),
  sourceYield: vi.fn(),
  cluster: vi.fn(),
  startPipeline: vi.fn(),
}));

vi.mock("./api", () => ({ api: mockedApi }));

import App from "./App";

const opportunity = {
  cluster_id: 33,
  title: "Reconcile delivery orders",
  category: "Business Operations",
  status: "active",
  created_at: "2026-10-19T10:00:00Z",
  updated_at: "2026-10-20T10:00:00Z",
  score: 62,
  evidence_level: "single_source_candidate",
  software_buildability: "likely",
  software_buildability_reasons: ["A bounded software workflow is plausible."],
  eligibility: "eligible",
  raw_attached_record_count: 1,
  analyzed_submission_count: 1,
  unique_evidence_count: 1,
  eligible_evidence_count: 1,
  unique_source_platform_count: 1,
  reasons: ["Specific repeated reconciliation work."],
  limitations: ["Single record; demand is not validated."],
  evidence_provenance: [],
};

const run = {
  run_id: "run-1234-5678",
  status: "completed" as const,
  requested_sources: ["hacker_news"],
  stage_counts: {},
  failures: [],
  started_at: "2026-10-20T10:00:00Z",
  completed_at: "2026-10-20T10:05:00Z",
  collected_submission_ids: [101],
  submissions: [{
    submission_id: 101,
    source_name: "Hacker News",
    source_url: "https://news.ycombinator.com/item?id=101",
    analysis_status: "completed",
    clustering_status: "clustered",
    cluster_id: 33,
    source_metadata: {},
  }],
  opportunity_clusters: [{
    cluster_id: 33,
    title: opportunity.title,
    run_submission_ids: [101],
    run_evidence_count: 1,
    total_attached_evidence_count: 1,
    contains_evidence_outside_run: false,
    opportunity_summary: opportunity,
  }],
  review_candidate_cluster_ids: [],
};

const clusterDetail = {
  id: 33,
  title: opportunity.title,
  problem_statement: "Delivery teams reconcile order status manually across tools.",
  affected_user: "Small delivery operators",
  category: opportunity.category,
  industry: "Logistics",
  status: "active",
  evidence_count: 1,
  unique_evidence_count: 1,
  duplicate_evidence_count: 0,
  verified_independent_evidence_count: 0,
  evidence_provenance: [{
    evidence_id: 8,
    submission_id: 101,
    source_name: "Hacker News",
    source_url: "https://news.ycombinator.com/item?id=101",
    created_at: "2026-10-20T10:00:00Z",
    submission_created_at: "2026-10-19T10:00:00Z",
    analysis_status: "completed",
    clustering_status: "clustered",
    relationship_type: "supports",
    recorded_independent: false,
    verified_independent: false,
    duplicate_of_submission_id: null,
    duplicate_reasons: [],
    scoring_status: "included",
    source_observations: [],
  }],
  opportunity_summary: opportunity,
};

function setDefaultResponses() {
  mockedApi.health.mockResolvedValue({ status: "ok", service: "local", version: "1.0" });
  mockedApi.opportunities.mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
  mockedApi.pipelineStatus.mockResolvedValue({
    run_lock_held: false,
    lock_lease_expired: false,
    owner_liveness_unknown: false,
    run_started_at: null,
    lease_expires_at: null,
    analysis_status_counts: {},
    clustering_status_counts: {},
    run_history_available: true,
  });
  mockedApi.pipelineRuns.mockResolvedValue({ items: [], total: 0, limit: 8, offset: 0 });
  mockedApi.pipelineRun.mockResolvedValue(run);
  mockedApi.sourceYield.mockResolvedValue({ communities: [] });
  mockedApi.cluster.mockResolvedValue(clusterDetail);
  mockedApi.startPipeline.mockResolvedValue({
    run_id: run.run_id,
    status: "completed",
    collected_submission_ids: [101],
    failures: [],
  });
}

describe("Fieldnotes workspace", () => {
  beforeEach(() => {
    window.location.hash = "#/discover";
    vi.clearAllMocks();
    setDefaultResponses();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders an honest empty state and never starts collection on page load", async () => {
    render(<App />);
    expect(await screen.findByText("No opportunities yet")).toBeInTheDocument();
    expect(screen.getByText("No runs recorded")).toBeInTheDocument();
    expect(mockedApi.startPipeline).not.toHaveBeenCalled();
  });

  it("shows populated overview data and handles unavailable API sections", async () => {
    mockedApi.opportunities.mockResolvedValue({
      items: [opportunity],
      total: 1,
      limit: 100,
      offset: 0,
    });
    mockedApi.pipelineRuns.mockRejectedValue(new Error("Run history is unavailable."));
    render(<App />);
    expect((await screen.findAllByText("Reconcile delivery orders")).length).toBeGreaterThan(0);
    expect(screen.getByText("Run history: Run history is unavailable.")).toBeInTheDocument();
    expect(mockedApi.startPipeline).not.toHaveBeenCalled();
  });

  it("filters loaded opportunities and navigates to source-backed detail", async () => {
    const user = userEvent.setup();
    mockedApi.opportunities.mockResolvedValue({
      items: [
        opportunity,
        { ...opportunity, cluster_id: 34, title: "Schedule clinic appointments", category: "Healthcare", score: null },
      ],
      total: 2,
      limit: 100,
      offset: 0,
    });
    render(<App />);

    await user.click(await screen.findByRole("link", { name: /Opportunities/ }));
    const search = await screen.findByRole("textbox", { name: "Search title and category" });
    await user.type(search, "clinic");
    expect(screen.getByText("Schedule clinic appointments")).toBeInTheDocument();
    expect(screen.queryByText("Reconcile delivery orders")).not.toBeInTheDocument();

    await user.clear(search);
    await user.click(screen.getByRole("link", { name: /Reconcile delivery orders/ }));
    expect(await screen.findByText("Delivery teams reconcile order status manually across tools.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open original source/ })).toHaveAttribute(
      "href",
      "https://news.ycombinator.com/item?id=101",
    );
    expect(mockedApi.cluster).toHaveBeenCalledWith(33);
  });

  it("constructs a real-schema run request only after an explicit submit", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("link", { name: /Sources & runs/ }));
    expect(mockedApi.startPipeline).not.toHaveBeenCalled();

    await user.click(screen.getByRole("checkbox", { name: "Hacker News" }));
    await user.click(screen.getByRole("checkbox", { name: /X/ }));
    await user.click(screen.getByRole("button", { name: "Start run" }));

    await waitFor(() => expect(mockedApi.startPipeline).toHaveBeenCalledTimes(1));
    expect(mockedApi.startPipeline).toHaveBeenCalledWith({
      sources: ["reddit", "x"],
      hacker_news_limit: 10,
      stack_exchange_limit: 10,
      stack_exchange_site: "stackoverflow",
      reddit_limit: 5,
      x_limit: 50,
      analysis_limit: 10,
      clustering_limit: 10,
    });
    expect(await screen.findByText("Run response received")).toBeInTheDocument();
    expect(screen.getAllByText(run.run_id).length).toBeGreaterThan(0);
    expect(mockedApi.pipelineRun).toHaveBeenCalledWith(run.run_id);
  });

  it("does not show a successful run after a failed start request", async () => {
    const user = userEvent.setup();
    mockedApi.startPipeline.mockRejectedValue(new Error("Provider is not configured."));
    render(<App />);
    await user.click(await screen.findByRole("link", { name: /Sources & runs/ }));
    await user.click(screen.getByRole("button", { name: "Start run" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Provider is not configured.");
    expect(screen.queryByText("Run response received")).not.toBeInTheDocument();
  });
});
