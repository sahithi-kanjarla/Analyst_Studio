import { useEffect, useMemo, useState } from "react";
import { api, RunSummary } from "../api";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "done", label: "Completed" },
  { id: "running", label: "In progress" },
  { id: "error", label: "Failed" },
] as const;
type Filter = (typeof FILTERS)[number]["id"];

const STATUS_LABEL: Record<string, string> = {
  done: "Completed",
  running: "In progress",
  queued: "Queued",
  needs_input: "Needs your input",
  error: "Failed",
};

// the backend stamps `created` in epoch seconds; tolerate milliseconds too
function toDate(t?: number): Date | null {
  if (!t) return null;
  return new Date(t < 1e12 ? t * 1000 : t);
}

export function when(t?: number): string {
  const d = toDate(t);
  if (!d) return "";
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function bucket(t?: number): string {
  const d = toDate(t);
  if (!d) return "Earlier";
  const days = Math.floor((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "This week";
  return "Earlier";
}

export function StatusBadge({ status }: { status: string }) {
  const kind = status === "done" ? "ok" : status === "error" ? "err" : "run";
  return (
    <span className={`status status-${kind}`}>
      <i />
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

export default function History() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    api.listRuns().then(setRuns).catch(() => setFailed(true));
  }, []);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (runs ?? []).filter((r) => {
      const st = r.status === "queued" || r.status === "needs_input" ? "running" : r.status;
      if (filter !== "all" && st !== filter) return false;
      return !needle || `${r.goal ?? ""} ${r.filename ?? ""}`.toLowerCase().includes(needle);
    });
  }, [runs, q, filter]);

  const groups = useMemo(() => {
    const out: Record<string, RunSummary[]> = {};
    for (const r of shown) (out[bucket(r.created)] ||= []).push(r);
    return ["Today", "Yesterday", "This week", "Earlier"].filter((g) => out[g]).map((g) => [g, out[g]] as const);
  }, [shown]);

  return (
    <div className="history">
      <div className="page-head">
        <div>
          <h2>History</h2>
          <p className="muted">Every analysis you have run. Open one to see its dashboard, insights and report again.</p>
        </div>
        <a className="primary small" href="#/">+ New analysis</a>
      </div>

      <div className="card hist-card">
        <div className="hist-bar">
          <div className="seg">
            {FILTERS.map((f) => (
              <button key={f.id} className={filter === f.id ? "seg-on" : ""} onClick={() => setFilter(f.id)}>
                {f.label}
              </button>
            ))}
          </div>
          <input className="hist-search" type="search" placeholder="Search by question or file name" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search history" />
        </div>

        {failed && <div className="error">Could not load history. Is the backend running?</div>}
        {!runs && !failed && <p className="muted hist-empty">Loading…</p>}
        {runs && shown.length === 0 && (
          <p className="muted hist-empty">{runs.length === 0 ? "No analyses yet. Upload a file to run your first one." : "Nothing matches your search."}</p>
        )}

        {groups.map(([name, items]) => (
          <section key={name} className="hist-group">
            <h4>{name}</h4>
            {items.map((r) => (
              <a key={r.run_id} className="hist-row" href={`#/run/${r.run_id}`}>
                <div className="hist-main">
                  <span className="hist-goal">{r.goal || "Exploratory analysis"}</span>
                  <span className="hist-sub">{r.filename || "Uploaded data"}</span>
                </div>
                <StatusBadge status={r.status} />
                <span className="hist-when">{when(r.created)}</span>
                <span className="hist-go" aria-hidden>›</span>
              </a>
            ))}
          </section>
        ))}
        {runs && runs.length >= 20 && <p className="muted small hist-note">Showing your 20 most recent analyses.</p>}
      </div>
    </div>
  );
}
