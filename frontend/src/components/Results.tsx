import { useEffect, useRef, useState } from "react";
import { api, RunResult } from "../api";
import { Tab, TAB_LABEL } from "../tabs";

import AnswersReview from "./AnswersReview";
import DataModel from "./DataModel";
import BIDashboard from "./BIDashboard";
import DataViewer from "./DataViewer";
import InsightCard from "./InsightCard";
import Tiers from "./Tiers";
import StrategyView from "./StrategyView";
import Md from "./Md";
import ReportPage from "./ReportPage";
import LensChat from "./lens/LensChat";
import { useLens } from "./lens/LensContext";
import { TabViewBar } from "./view/ViewUI";
import { useView } from "./view/ViewContext";

const TABS: { id: Tab; label: string }[] = (["report", "dashboard", "insights", "strategy", "data", "model", "cleaning", "plan", "powerbi", "chat"] as Tab[]).map((id) => ({ id, label: TAB_LABEL[id] }));

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
  const [picked, setPicked] = useState<Tab>("report");
  const lens = useLens();
  const tab: Tab = lens?.expanded ? "chat" : picked;       // "Expand" in the Lens panel opens the same conversation as this tab
  const setTab = (t: Tab) => {
    lens?.setExpanded(t === "chat");
    if (t === "chat") lens?.setOpen(false);
    else setPicked(t);
  };
  const setTabName = lens?.setTabName;
  useEffect(() => { setTabName?.(TAB_LABEL[tab]); }, [tab, setTabName]);
  const card = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(false);
  const [review, setReview] = useState(false);
  useEffect(() => {
    const on = () => setFull(document.fullscreenElement === card.current);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);
  const toggleFull = () => (document.fullscreenElement ? document.exitFullscreen() : card.current?.requestFullscreen());
  const warns = run.critique?.issues.filter((i) => i.severity === "warn").length ?? 0;

  return (
    <div className={`card results ${full ? "is-full" : ""}`} ref={card}>
      <div className="results-actions">
        <button className="btn-quiet" onClick={() => setReview((v) => !v)} title="See, change or re-ask the answers this run used">Review answers</button>
        <button className="btn-quiet hide-phone" onClick={toggleFull} title="Full screen (Esc to exit)">{full ? "Exit full screen" : "Full screen"}</button>
        <a className="btn" href={api.powerbiUrl(run.run_id)}>Download Power BI pack</a>
      </div>
      <div className="tabs">
        {TABS.filter((t) => (t.id !== "model" || run.data_model) && (t.id !== "strategy" || run.strategies?.length) && (t.id !== "chat" || lens?.enabled)).map((t) => (
          <button key={t.id} className={`tab ${tab === t.id ? "tab-active" : ""}`} onClick={() => setTab(t.id)}>
            {t.label}
            {t.id === "insights" && <span className="count">{run.report_view ? run.report_view.evidence.length : (run.findings ?? []).filter((f) => f.insight).length}</span>}
            {t.id === "strategy" && <span className="count">{run.report_view ? run.report_view.strategies.strategies.length : run.strategies?.length ?? 0}</span>}
            {t.id === "report" && warns > 0 && <span className="count count-warn">{warns}</span>}
          </button>
        ))}
      </div>

      {review && <AnswersReview runId={run.run_id} onClose={() => setReview(false)} />}

      {tab !== "dashboard" && tab !== "report" && tab !== "chat" && run.key_metrics && run.key_metrics.length > 0 && (
        <div className="kpi-strip">
          {run.key_metrics.map((m) => (
            <div className="kpi" key={m.label} title={m.note}>
              <span className="kpi-val">{m.value}</span>
              <span className="kpi-label">{m.label}</span>
            </div>
          ))}
        </div>
      )}

      <TabBody run={run} tab={tab} />
    </div>
  );
}

/** The report as written, with its collapsed sections. Used by the results tab and the landing tour. */
export function ReportBody({ md }: { md: string }) {
  return (
    <article className="report report-page">
      <Md md={md} />
    </article>
  );
}

/** The cleaning log: every change made to the data, with the rows or values it touched. Used by the results tabs and the landing tour. */
export function CleaningLog({ run }: { run: RunResult }) {
  const v = useView();
  const shown = (run.cleaning_log ?? []).map((s, i) => ({ s, i })).filter(({ i }) => !v?.isHidden(`cleaning:step:${i}`));
  return (
    <>
    <TabViewBar tab="cleaning" />
<table className="table">
    <thead>
      <tr>
        <th>Step</th>
        <th>What was done</th>
        <th>Rows / values</th>
      </tr>
    </thead>
    <tbody>
      {shown.map(({ s, i }) => (
        <tr key={i} data-view-id={`cleaning:step:${i}`}>
          <td>{s.step}</td>
          <td>{s.detail}</td>
          <td>{s.count.toLocaleString()}</td>
        </tr>
      ))}
      {!shown.length && (
        <tr>
          <td colSpan={3}>{run.cleaning_log?.length ? "Every step is hidden. Restore them from Hidden items." : "No changes were needed."}</td>
        </tr>
      )}
    </tbody>
  </table>
    </>
  );
}

/** The plan: the questions the analysis set out to answer, the analyses chosen and the decisions taken. */
export function PlanView({ run }: { run: RunResult }) {
  const v = useView();
  if (!run.plan) return null;
  return (
<div className="plan">
    <TabViewBar tab="plan" />
    <p>
      <strong>Plan made by:</strong> {run.plan.source === "llm" || run.plan.source === "repaired" ? "the AI" : "our built-in rules"} · <strong>Number we focus on:</strong> {run.plan.target ?? "none"} ·{" "}
      <strong>Date used:</strong> {run.plan.date_col ?? "none"}
    </p>
    <h4>Questions we set out to answer</h4>
    <ul>{run.plan.questions.map((q, i) => !v?.isHidden(`plan:question:${i}`) && <li key={q} data-view-id={`plan:question:${i}`}>{q}</li>)}</ul>
    <h4>What we checked, and why</h4>
    <p className="muted">Some checks were suggested by the AI. Others were added by code because they are always needed.</p>
    <ol>
      {run.plan.analyses.map((a, i) => !v?.isHidden(`plan:analysis:${i}`) && (
        <li key={i} data-view-id={`plan:analysis:${i}`}>
          <strong>{a.tool.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())}</strong> {Object.values(a.params).flat().map(String).join(", ")}
          {a.origin && <span className="muted"> · <strong>{ORIGIN_LABEL[a.origin]}</strong></span>}
          <div className="muted">{a.why}</div>
        </li>
      ))}
    </ol>
    {run.decisions && run.decisions.length > 0 && (
      <>
        <h4>Choices made along the way</h4>
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
        <h4>What we left out</h4>
        <ul>{run.plan.skipped.map((s) => <li key={s.column}><code>{s.column}</code>: {s.reason}</li>)}</ul>
      </>
    )}
  </div>
  );
}

/** One tab's content. The results page and the start page tour both render it, so the tour always shows the real screen. */
export function TabBody({ run, tab, embedded = false, startChat = false, startPrint = false }: { run: RunResult; tab: Tab; embedded?: boolean; startChat?: boolean; startPrint?: boolean }) {
  const ranked = [...(run.findings ?? [])].filter((f) => f.insight).sort((a, b) => (b.insight!.score - a.insight!.score));
  return (
    <>
      {tab === "dashboard" && <BIDashboard run={run} />}

      {tab === "chat" && <LensChat variant="tab" />}

      {tab === "data" && <DataViewer runId={run.run_id} />}

      {tab === "report" && <ReportPage run={run} embedded={embedded} startChat={startChat} startPrint={startPrint} />}

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
    </>
  );
}
