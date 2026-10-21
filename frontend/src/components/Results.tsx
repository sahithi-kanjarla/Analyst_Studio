import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, RunResult } from "../api";

import DataModel from "./DataModel";
import BIDashboard from "./BIDashboard";
import DataViewer from "./DataViewer";
import InsightCard from "./InsightCard";
import StrategyView from "./StrategyView";

type Tab = "dashboard" | "report" | "insights" | "strategy" | "data" | "model" | "cleaning" | "plan" | "powerbi";
const TABS: { id: Tab; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "insights", label: "Insights" },
  { id: "strategy", label: "Strategy" },
  { id: "report", label: "Report" },
  { id: "data", label: "Data" },
  { id: "model", label: "Data model" },
  { id: "cleaning", label: "Cleaning log" },
  { id: "plan", label: "Plan" },
  { id: "powerbi", label: "Power BI" },
];

export default function Results({ run }: { run: RunResult }) {
  const [tab, setTab] = useState<Tab>("dashboard");
  const card = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(false);
  useEffect(() => {
    const on = () => setFull(document.fullscreenElement === card.current);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);
  const toggleFull = () => (document.fullscreenElement ? document.exitFullscreen() : card.current?.requestFullscreen());
  const ranked = [...(run.findings ?? [])].filter((f) => f.insight).sort((a, b) => (b.insight!.score - a.insight!.score));
  const warns = run.critique?.issues.filter((i) => i.severity === "warn").length ?? 0;

  return (
    <div className={`card results ${full ? "is-full" : ""}`} ref={card}>
      <div className="tabs">
        {TABS.filter((t) => (t.id !== "model" || run.data_model) && (t.id !== "strategy" || run.strategies?.length)).map((t) => (
          <button key={t.id} className={`tab ${tab === t.id ? "tab-active" : ""}`} onClick={() => setTab(t.id)}>
            {t.label}
                        {t.id === "insights" && <span className="count">{ranked.length}</span>}
            {t.id === "strategy" && <span className="count">{run.strategies?.length ?? 0}</span>}
            {t.id === "report" && warns > 0 && <span className="count count-warn">{warns}</span>}
          </button>
        ))}
        <span className="spacer" />
        <button className="chip" onClick={toggleFull} title="Full screen (Esc to exit)">{full ? "⤢ Exit full screen" : "⛶ Full screen"}</button>
        <a className="primary small" href={api.powerbiUrl(run.run_id)}>
          Download Power BI pack
        </a>
      </div>

      {tab !== "dashboard" && run.key_metrics && run.key_metrics.length > 0 && (
        <div className="kpi-strip">
          {run.key_metrics.map((m) => (
            <div className="kpi" key={m.label} title={m.note}>
              <span className="kpi-val">{m.value}</span>
              <span className="kpi-label">{m.label}</span>
            </div>
          ))}
        </div>
      )}

      {tab === "dashboard" && <BIDashboard run={run} />}

      {tab === "data" && <DataViewer runId={run.run_id} />}

      {tab === "report" && (
        <article className="report">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{run.report_md ?? ""}</ReactMarkdown>
        </article>
      )}

      {tab === "insights" && (
        <div className="insights">
          {run.context?.company && <p className="muted">Tailored for <strong>{run.context.company}</strong>{run.context.explore ? " (exploratory run: no specific question was set)" : ""}.</p>}
          {ranked.map((f, i) => <InsightCard key={f.id} f={f} index={i + 1} />)}
          {!ranked.length && <p>No insights were produced.</p>}
        </div>
      )}

      {tab === "strategy" && run.strategies && (
        <StrategyView strategies={run.strategies} summary={run.strategy_summary ?? ""} gaps={run.data_gaps ?? []} findings={run.findings ?? []} />
      )}

      {tab === "model" && run.data_model && <DataModel model={run.data_model} />}

      {tab === "cleaning" && (
        <table className="table">
          <thead>
            <tr>
              <th>Step</th>
              <th>What was done</th>
              <th>Rows / values</th>
            </tr>
          </thead>
          <tbody>
            {run.cleaning_log?.map((s, i) => (
              <tr key={i}>
                <td>{s.step}</td>
                <td>{s.detail}</td>
                <td>{s.count.toLocaleString()}</td>
              </tr>
            ))}
            {!run.cleaning_log?.length && (
              <tr>
                <td colSpan={3}>No changes were needed.</td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {tab === "plan" && run.plan && (
        <div className="plan">
          <p>
            <strong>Planned by:</strong> {run.plan.source === "llm" ? `LLM (${run.llm_mode})` : "rule-based planner"} · <strong>Target:</strong> {run.plan.target ?? "none"} ·{" "}
            <strong>Date column:</strong> {run.plan.date_col ?? "none"}
          </p>
          <h4>Questions the agent set out to answer</h4>
          <ul>{run.plan.questions.map((q) => <li key={q}>{q}</li>)}</ul>
          <h4>Analyses chosen (and why)</h4>
          <ol>
            {run.plan.analyses.map((a, i) => (
              <li key={i}>
                <code>{a.tool}</code> {Object.values(a.params).flat().map(String).join(", ")}
                <div className="muted">{a.why}</div>
              </li>
            ))}
          </ol>
          {run.plan.skipped.length > 0 && (
            <>
              <h4>Deliberately skipped</h4>
              <ul>{run.plan.skipped.map((s) => <li key={s.column}><code>{s.column}</code>: {s.reason}</li>)}</ul>
            </>
          )}
        </div>
      )}

      {tab === "powerbi" && run.powerbi && (
        <div className="powerbi">
          <p>
            The pack contains <code>{run.powerbi.files.join("</code>, <code>")}</code>. Open <code>POWERBI_GUIDE.md</code> first: load the CSV in Power BI Desktop, paste the measures, apply the theme, then build the visuals below.
          </p>
          <h4>DAX measures</h4>
          <pre>{run.powerbi.measures.join("\n\n")}</pre>
          <h4>Suggested visuals</h4>
          <table className="table">
            <thead>
              <tr><th>Visual</th><th>Axis</th><th>Values</th><th>Tip</th></tr>
            </thead>
            <tbody>
              {run.powerbi.visuals.map((v, i) => (
                <tr key={i}><td>{v.visual}</td><td>{v.axis}</td><td>{v.values}</td><td>{v.tip}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
