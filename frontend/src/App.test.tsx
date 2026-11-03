import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockedApi = vi.hoisted(() => ({
  leads: vi.fn(),
  startPipeline: vi.fn(),
  recordFeedback: vi.fn(),
}));

vi.mock("./api", () => ({ api: mockedApi }));

import App from "./App";

const lead = {
  cluster_id: 26,
  lead_id: 26,
  title: "AI-Resilient Data Moat for B2B Marketplaces",
  category: "IndiaMART",
  status: "new",
  created_at: "2026-10-30T10:00:00Z",
  score: 93.8,
  evidence_level: "lead",
  why_now: "Google AI Overviews expanded to 50%+ of searches.",
  first_customer_reach_strategy: "Target CMOs at mid-tier B2B marketplaces via LinkedIn.",
  delivery_type: "service",
  raw_attached_record_count: 1,
  unique_evidence_count: 1,
  problem_statement: "Verified supplier data locked behind login walls.",
  smallest_first_version: "Login-wall crawl that scores supplier profile completeness.",
  source_url: "https://yourstory.com/a1",
  corroborating_urls: ["https://inc42.com/a2"],
  evidence_citations: ["IndiaMART has moved content behind logins."],
  evidence_quotes: ["IndiaMART has moved content behind logins."],
};

describe("Business leads dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.leads.mockResolvedValue({ items: [lead], total: 1, limit: 100, offset: 0 });
    mockedApi.startPipeline.mockResolvedValue({ run_id: "run-1", status: "completed" });
    mockedApi.recordFeedback.mockResolvedValue({ recorded: true });
  });

  afterEach(() => {
    cleanup();
  });

  it("renders executive summary and does not run pipeline on load", async () => {
    render(<App />);
    expect(await screen.findByText(/1 new business lead/)).toBeInTheDocument();
    expect(screen.getByText(/Top opportunity:/)).toBeInTheDocument();
    expect(mockedApi.startPipeline).not.toHaveBeenCalled();
  });

  it("shows lead cards with why now and first customer strategy", async () => {
    render(<App />);
    expect(await screen.findByText(lead.title)).toBeInTheDocument();
    expect(screen.getByText(/Google AI Overviews/)).toBeInTheDocument();
    expect(screen.getByText(/Target CMOs/)).toBeInTheDocument();
    expect(screen.getByText(/What to Build \(v1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Login-wall crawl/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "https://yourstory.com/a1" })).toHaveAttribute(
      "href",
      "https://yourstory.com/a1",
    );
    expect(screen.getByRole("link", { name: "https://inc42.com/a2" })).toHaveAttribute(
      "href",
      "https://inc42.com/a2",
    );
  });

  it("labels the hero grid as top 5 opportunities", async () => {
    render(<App />);
    expect(await screen.findByText(/Top 5 Opportunities/)).toBeInTheDocument();
  });

  it("submits accept feedback with a mandatory reason", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(lead.title);
    await user.click(screen.getByRole("button", { name: "Accept" }));
    await user.type(
      screen.getByPlaceholderText(/Reason for accepting/),
      "Strong India buyer signal",
    );
    await user.click(screen.getByRole("button", { name: "Submit" }));
    await waitFor(() =>
      expect(mockedApi.recordFeedback).toHaveBeenCalledWith(
        26,
        "accepted",
        "Strong India buyer signal",
      ),
    );
    expect(await screen.findByText("✓ Feedback recorded")).toBeInTheDocument();
  });
});
