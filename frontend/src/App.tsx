import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { api, type LeadDashboardItem } from "./api";
import "./styles.css";

function timeAgo(dateString: string | null) {
  if (!dateString) return "Unknown date";
  const days = Math.floor(
    (Date.now() - new Date(dateString).getTime()) / (1000 * 3600 * 24),
  );
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return `${days} days ago`;
}

function FeedbackUI({ leadId }: { leadId: number }) {
  const [mode, setMode] = useState<"accepted" | "rejected" | null>(null);
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "done">("idle");

  const submit = async () => {
    if (!reason.trim() || !mode) return;
    setStatus("loading");
    try {
      await api.recordFeedback(leadId, mode, reason);
      setStatus("done");
    } catch {
      alert("Failed to save feedback");
      setStatus("idle");
    }
  };

  if (status === "done") {
    return <div className="feedback-success">✓ Feedback recorded</div>;
  }

  if (mode) {
    return (
      <div className="feedback-form">
        <input
          autoFocus
          className="feedback-input"
          placeholder={`Reason for ${mode === "accepted" ? "accepting" : "rejecting"}...`}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        <div className="feedback-actions">
          <button type="button" className="btn-feedback" onClick={() => setMode(null)}>
            Cancel
          </button>
          <button
            type="button"
            className="feedback-submit"
            onClick={submit}
            disabled={status === "loading"}
          >
            {status === "loading" ? "Saving..." : "Submit"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="feedback-actions">
      <button type="button" className="btn-feedback accept" onClick={() => setMode("accepted")}>
        Accept
      </button>
      <button type="button" className="btn-feedback reject" onClick={() => setMode("rejected")}>
        Reject
      </button>
    </div>
  );
}

export default function App() {
  const [leads, setLeads] = useState<LeadDashboardItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try {
      const data = await api.leads();
      setLeads(data.items || []);
    } catch (e) {
      console.error(e);
      setLeads([]);
    }
    setLoading(false);
  };

  useEffect(() => {
    void fetchData();
  }, []);

  const handleRunPipeline = async () => {
    setRunning(true);
    try {
      await api.startPipeline();
      await fetchData();
    } catch {
      alert("Pipeline run failed. Check console.");
    }
    setRunning(false);
  };

  const ranked = leads.filter((l) => l.score !== null);
  const top5 = ranked.slice(0, 5);
  const watchlist = ranked.slice(5);

  const isHttpUrl = (value: string | null | undefined): value is string => {
    if (!value) return false;
    try {
      const parsed = new URL(value);
      return parsed.protocol === "http:" || parsed.protocol === "https:";
    } catch {
      return false;
    }
  };

  return (
    <div className="app-container">
      <header className="header">
        <div>
          <div className="brand">
            <div className="brand-dot" />
            TATVIC LABS
          </div>

          <motion.div
            className="hero-summary"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
          >
            {top5.length === 0 ? (
              <p>
                <strong>Nothing worth looking at this week.</strong>
              </p>
            ) : (
              <>
                <p>
                  <strong>{top5.length} new business lead(s)</strong> surfaced this week.
                </p>
                <p>Top opportunity: &quot;{top5[0].title}&quot;</p>
                <p>
                  {watchlist.length} previous/lower-scoring ideas monitored in the watchlist.
                </p>
              </>
            )}
          </motion.div>
        </div>

        <button
          type="button"
          className="btn-run"
          onClick={handleRunPipeline}
          disabled={running || loading}
        >
          {running ? "Running Discovery..." : "Run Weekly Discovery"}
        </button>
      </header>

      {loading ? (
        <p style={{ color: "var(--text-muted)" }}>Scanning market data...</p>
      ) : (
        <>
          <h2 className="section-title">
            Top 5 Opportunities <span className="badge-count">{top5.length}</span>
          </h2>

          <div className="leads-grid">
            {top5.map((lead, i) => (
              <motion.article
                key={lead.lead_id}
                className="lead-card"
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: i * 0.05 }}
              >
                <div className="lead-meta">
                  <span className="pill primary">{lead.score} / 100</span>
                  <span className="pill">{lead.delivery_type || "service"}</span>
                </div>

                <h3>{lead.title}</h3>

                <ul className="lead-facts">
                  <li>
                    <strong>Why Now?</strong> {lead.why_now || "Unclear market trigger."}
                  </li>
                  <li>
                    <strong>First Customer Strategy</strong>{" "}
                    {lead.first_customer_reach_strategy || "Needs research."}
                  </li>
                  <li>
                    <strong>What to Build (v1)</strong>{" "}
                    {lead.smallest_first_version || "Needs definition."}
                  </li>
                  <li>
                    <strong>Source Citations</strong>
                    {isHttpUrl(lead.source_url) ||
                    (lead.corroborating_urls || []).some(isHttpUrl) ? (
                      <span className="citation-links">
                        {isHttpUrl(lead.source_url) ? (
                          <a
                            href={lead.source_url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            {lead.source_url}
                          </a>
                        ) : null}
                        {(lead.corroborating_urls || []).filter(isHttpUrl).map((url) => (
                          <a
                            key={url}
                            href={url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            {url}
                          </a>
                        ))}
                      </span>
                    ) : (
                      <em>See attached source records.</em>
                    )}
                  </li>
                </ul>

                <div className="feedback-ui">
                  <FeedbackUI leadId={lead.lead_id} />
                </div>
              </motion.article>
            ))}
          </div>

          {watchlist.length > 0 && (
            <>
              <h2 className="section-title">
                Watchlist &amp; All Problems{" "}
                <span className="badge-count">{watchlist.length}</span>
              </h2>
              <div className="watchlist">
                {watchlist.map((item, i) => (
                  <motion.div
                    key={item.lead_id}
                    className="watch-row"
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.02 }}
                  >
                    <div>
                      <h4>{item.title}</h4>
                      <div className="watch-meta">
                        <span>Surfaced {timeAgo(item.created_at)}</span>
                        <span>•</span>
                        <span>{item.unique_evidence_count} evidence citations</span>
                        <span>•</span>
                        <span>{item.category || "Uncategorized"}</span>
                      </div>
                    </div>
                    <div className="watch-score">
                      {item.score ?? "—"}
                      <small>SCORE</small>
                    </div>
                  </motion.div>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
