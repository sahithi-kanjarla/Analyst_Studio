import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";
import { api, RunResult } from "../api";

import AnswersReview from "./AnswersReview";
import DataModel from "./DataModel";
import BIDashboard from "./BIDashboard";
import DataViewer from "./DataViewer";
import InsightCard from "./InsightCard";
import Tiers from "./Tiers";
import StrategyView from "./StrategyView";
import PrintPanel from "./PrintPanel";
import AskReport from "./AskReport";

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

const DECISION_LABEL: Record<string, string> = {
  llm: "chosen by AI",
  repaired: "chosen by AI (repaired once)",
  fallback: "fallback rule used",
  "rules-only": "rules only (no AI)",
};

const ORIGIN_LABEL: Record<string, string> = { ai: "Chosen by AI", code: "Added by code", rules: "Chosen by rules" };

function summariseChosen(v: unknown): string {
  if (v && typeof v === "object" && !Array.isArray(v)) return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k}: ${String(x)}`).join(", ");
  return String(v ?? "");
}

export default function Results({ run }: { run: RunResult }) {
  const [tab, setTab] = useState<Tab>("dashboard");
  const card = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(false);
  const [review, setReview] = useState(false);
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
                        {t.id === "insights" && <span className="count">{run.report_view ? run.report_view.evidence.length : ranked.length}</span>}
            {t.id === "strategy" && <span className="count">{run.report_view ? run.report_view.strategies.strategies.length : run.strategies?.length ?? 0}</span>}
            {t.id === "report" && warns > 0 && <span className="count count-warn">{warns}</span>}
          </button>
        ))}
        <span className="spacer" />
        {run.run_mode && <span className={`pill ${run.run_mode === "AI-assisted" ? "pill-ok" : run.run_mode === "AI-assisted (partly)" ? "" : "pill-muted"}`} title="How much of this run was decided by the AI versus fixed rules">{run.run_mode}</span>}
        <button className="chip" onClick={() => setReview((v) => !v)} title="See, change or re-ask the answers this run used">Review answers</button>
        <button className="chip" onClick={toggleFull} title="Full screen (Esc to exit)">{full ? "⤢ Exit full screen" : "⛶ Full screen"}</button>
        <a className="primary small" href={api.powerbiUrl(run.run_id)}>
          Download Power BI pack
        </a>
      </div>

      {review && <AnswersReview runId={run.run_id} onClose={() => setReview(false)} />}

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
        <>
          <PrintPanel run={run} />
          <AskReport run={run} />
          <ReportBody md={run.report_md ?? ""} />
        </>
      )}

      {tab === "insights" && (
        <div className="insights">
          {run.context?.company && <p className="muted">Tailored for <strong>{run.context.company}</strong>{run.context.explore ? " (exploratory run: no specific question was set)" : ""}.</p>}
          {run.report_view ? <Tiers view={run.report_view} goalAnswer={run.goal_answer} /> : ranked.map((f, i) => <InsightCard key={f.id} f={f} index={i + 1} />)}
          {!ranked.length && <p>No insights were produced.</p>}
        </div>
      )}

      {tab === "strategy" && run.strategies && (
        <StrategyView strategies={run.report_view?.strategies.strategies ?? run.strategies} foundation={run.report_view?.strategies.foundation} dropped={run.report_view?.strategies.dropped ?? 0} summary={run.strategy_summary ?? ""} gaps={run.data_gaps ?? []} findings={run.findings ?? []} />
      )}

      {tab === "model" && run.data_model && <DataModel model={run.data_model} />}

      {tab === "cleaning" && <CleaningLog run={run} />}

      {tab === "plan" && run.plan && <PlanView run={run} />}

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

/** The report as written, with its collapsed sections. Used by the results tab and the landing tour. */
export function ReportBody({ md }: { md: string }) {
  return (
    <article className="report">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>{md}</ReactMarkdown>
    </article>
  );
}

/** The cleaning log: every change made to the data, with the rows or values it touched. Used by the results tabs and the landing tour. */
export function CleaningLog({ run }: { run: RunResult }) {
  return (
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
  );
}

/** The plan: the questions the analysis set out to answer, the analyses chosen and the decisions taken. */
export function PlanView({ run }: { run: RunResult }) {
  if (!run.plan) return null;
  return (
<div className="plan">
    <p>
      <strong>Planned by:</strong> {run.plan.source === "llm" || run.plan.source === "repaired" ? `LLM (${run.llm_mode})` : "rule-based planner"} · <strong>Target:</strong> {run.plan.target ?? "none"} ·{" "}
      <strong>Date column:</strong> {run.plan.date_col ?? "none"}
    </p>
    <h4>Questions the agent set out to answer</h4>
    <ul>{run.plan.questions.map((q) => <li key={q}>{q}</li>)}</ul>
    <h4>Analyses chosen (and why)</h4>
    <p className="muted">{ORIGIN_LABEL.ai}: picked by the model. {ORIGIN_LABEL.code}: required, so the code added it when the model left it out.</p>
    <ol>
      {run.plan.analyses.map((a, i) => (
        <li key={i}>
          <code>{a.tool}</code> {Object.values(a.params).flat().map(String).join(", ")}
          {a.origin && <span className="muted"> · <strong>{ORIGIN_LABEL[a.origin]}</strong></span>}
          <div className="muted">{a.why}</div>
        </li>
      ))}
    </ol>
    {run.decisions && run.decisions.length > 0 && (
      <>
        <h4>Decisions</h4>
        <ul>
          {run.decisions.map((d, i) => (
            <li key={i}>
              <strong>{d.decision}</strong>: {DECISION_LABEL[d.source] ?? d.source}
              <div className="muted">{summariseChosen(d.chosen)}{d.why ? ` · ${d.why}` : ""}{d.source === "fallback" && d.errors.length ? ` · ${d.errors.join("; ")}` : ""}</div>
            </li>
          ))}
        </ul>
      </>
    )}
    {run.plan.skipped.length > 0 && (
      <>
        <h4>Deliberately skipped</h4>
        <ul>{run.plan.skipped.map((s) => <li key={s.column}><code>{s.column}</code>: {s.reason}</li>)}</ul>
      </>
    )}
  </div>
  );
}
