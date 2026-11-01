import { RefObject, useEffect, useRef, useState } from "react";
import { api, RunResult } from "../api";
import { backendTab, DATA_LABEL, DATA_VIEWS, DataView, NAV_TABS, NavTab, resolveTarget, Tab, TAB_LABEL } from "../tabs";
import Dialog from "./Dialog";
import ExportMenu, { BriefsDialog, ExportItem } from "./nav/ExportMenu";
import RunBar, { TabInfo } from "./nav/RunBar";
import PrintPanel from "./PrintPanel";

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
import { ShowAllLink, TabViewBar, ViewItem } from "./view/ViewUI";
import { useView } from "./view/ViewContext";

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

const DETAILS_KEY = (runId: string) => `analyst-studio.details.${runId}`;
const NAV_KEY = (runId: string) => `analyst-studio.nav.${runId}`;

/** Opens (or folds back) every `<details>` under `root`: the sections the reader may skip. Each keeps the state it had when first seen, and goes back to it when the details are collapsed. */
function useFolds(root: RefObject<HTMLElement>, expanded: boolean, deps: unknown[]): number {
  const [count, setCount] = useState(0);
  const last = useRef<boolean | null>(null);
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const apply = () => {
      const all = Array.from(el.querySelectorAll<HTMLDetailsElement>("details"));
      const flipped = last.current !== null && last.current !== expanded;
      for (const d of all) {
        if (d.dataset.fold === undefined) {
          d.dataset.fold = d.open ? "open" : "closed";
          if (expanded) d.open = true;
        } else if (flipped) {
          d.open = expanded ? true : d.dataset.fold === "open";
        }
      }
      last.current = expanded;
      setCount(all.length);
    };
    apply();
    const mo = new MutationObserver(() => { mo.disconnect(); apply(); mo.observe(el, { childList: true, subtree: true }); });
    mo.observe(el, { childList: true, subtree: true });
    return () => mo.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, root, ...deps]);
  return count;
}

export default function Results({ run, target }: { run: RunResult; target?: string | null }) {
  const lens = useLens();
  const view = useView();
  const stored = (() => { try { return JSON.parse(window.localStorage.getItem(NAV_KEY(run.run_id)) ?? "null") as { data?: DataView } | null; } catch { return null; } })();
  const [nav, setNav] = useState<NavTab>("report");
  const [dataView, setDataView] = useState<DataView>(stored?.data && DATA_VIEWS.includes(stored.data) ? stored.data : "data");
  const [dialog, setDialog] = useState<ExportItem | null>(null);
  const chat = !!lens?.expanded;                      // "Expand" in the Lens panel opens the same conversation full width, in place of the page
  const backend: Tab = chat ? "chat" : backendTab(nav, dataView);
  const setTabName = lens?.setTabName;
  useEffect(() => { setTabName?.(TAB_LABEL[backend]); }, [backend, setTabName]);
  const pickDataView = (v: DataView) => {
    setDataView(v);
    try { window.localStorage.setItem(NAV_KEY(run.run_id), JSON.stringify({ data: v })); } catch { /* the sub-view is simply not remembered */ }
  };
  const pickTab = (t: NavTab) => {
    if (chat) lens?.setExpanded(false);
    setNav(t);
  };
  // Old links and names (#/run/<id>/<old tab>) arrive here: a tab, a Data sub-view, the Export menu or the expanded Lens.
  useEffect(() => {
    const t = resolveTarget(target);
    if (!t) return;
    setNav(t.nav);
    if (t.data) pickDataView(t.data);
    if (t.exportItem === "powerbi" || t.exportItem === "print" || t.exportItem === "briefs") setDialog(t.exportItem);
    if (t.lens) { lens?.setExpanded(true); lens?.setOpen(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  const card = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const [full, setFull] = useState(false);
  const [review, setReview] = useState(false);
  const dirty = useRef(false);
  const [leave, setLeave] = useState<string | null>(null);
  useEffect(() => {
    const on = () => setFull(document.fullscreenElement === card.current);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty.current) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);
  const toggleFull = () => (document.fullscreenElement ? document.exitFullscreen() : card.current?.requestFullscreen());
  const onLeave = (href: string) => {
    if (!dirty.current) return true;                  // unsaved answer changes: ask before going
    setLeave(href);
    return false;
  };
  const warns = run.critique?.issues.filter((i) => i.severity === "warn").length ?? 0;

  // Expand all details: kept with this report's view (so Lens can set it too: "show all details"); without view edits, in this browser.
  const [localDetails, setLocalDetails] = useState(() => { try { return window.localStorage.getItem(DETAILS_KEY(run.run_id)) === "1"; } catch { return false; } });
  const expanded = view ? view.details : localDetails;
  const setExpanded = (on: boolean) => {
    if (view) void view.setDetails(on);
    else {
      setLocalDetails(on);
      try { window.localStorage.setItem(DETAILS_KEY(run.run_id), on ? "1" : "0"); } catch { /* not remembered */ }
    }
  };
  const folds = useFolds(body, expanded, [backend, chat, run]);

  const tabs: TabInfo[] = NAV_TABS.map((id) => ({
    id,
    count: id === "insights" ? (run.report_view ? run.report_view.evidence.length : (run.findings ?? []).filter((f) => f.insight).length)
      : id === "strategy" ? (run.report_view ? run.report_view.strategies.strategies.length : run.strategies?.length ?? 0)
      : id === "report" && warns > 0 ? warns : undefined,
    warn: id === "report" && warns > 0,
  }));
  const name = run.report_view?.page?.company ? `${run.report_view.page.company} report` : run.context?.company ? `${run.context.company} report` : "Your report";

  return (
    <>
      <RunBar name={name} tabs={tabs} active={chat ? null : nav} onTab={pickTab} onLeave={onLeave} onReview={() => setReview(true)} onFullScreen={toggleFull} fullScreen={full}
        exportMenu={<ExportMenu run={run} onPick={(item) => { if (item === "print") setNav("report"); setDialog(item); }} />} />
      <div className={`card results ${full ? "is-full" : ""}`} ref={card}>
        {review && <AnswersReview runId={run.run_id} onClose={() => setReview(false)} onDirty={(d) => { dirty.current = d; }} />}
        {dialog === "briefs" && <BriefsDialog runId={run.run_id} onClose={() => setDialog(null)} />}
        {dialog === "powerbi" && (
          <Dialog title="Power BI files" intro="Everything you need to rebuild the charts in Power BI." onClose={() => setDialog(null)} wide>
            <p><a className="btn" href={api.powerbiUrl(run.run_id)}>Download Power BI pack</a></p>
            <TabBody run={run} tab="powerbi" />
          </Dialog>
        )}
        <PrintPanel run={run} dialog open={dialog === "print"} onClose={() => setDialog(null)} />
        {leave && (
          <Dialog title="Leave without saving?" intro="You changed some answers and did not re-run with them. Going now discards the changes." onClose={() => setLeave(null)}>
            <div className="qactions">
              <button className="chip" onClick={() => setLeave(null)}>Stay here</button>
              <button className="chip chip-danger" onClick={() => { dirty.current = false; const h = leave; setLeave(null); setReview(false); window.location.hash = h; }}>Leave</button>
            </div>
          </Dialog>
        )}

        {chat ? (
          <div ref={body}><TabBody run={run} tab="chat" /></div>
        ) : (
          <>
            {nav === "data" && (
              <div className="dv" role="tablist" aria-label="Data views">
                {DATA_VIEWS.map((v) => (
                  <button key={v} role="tab" type="button" aria-selected={dataView === v} className={`dv-item ${dataView === v ? "dv-on" : ""}`} onClick={() => pickDataView(v)}>{DATA_LABEL[v]}</button>
                ))}
              </div>
            )}
            <div className="page-tools no-print">
              <button type="button" className="btn-quiet details-toggle" aria-pressed={expanded} disabled={folds === 0 && !expanded} onClick={() => setExpanded(!expanded)}
                title={folds === 0 && !expanded ? "Nothing is folded away on this page" : expanded ? "Fold the supporting detail away again" : "Open every folded section on this page"}>
                {expanded ? "Collapse details" : "Expand all details"}
              </button>
            </div>
            {backend !== "dashboard" && backend !== "report" && run.key_metrics && run.key_metrics.length > 0 && (
              <div className="kpi-strip">
                {run.key_metrics.map((m) => (
                  <div className="kpi" key={m.label} title={m.note}>
                    <span className="kpi-val">{m.value}</span>
                    <span className="kpi-label">{m.label}</span>
                  </div>
                ))}
              </div>
            )}
            <div ref={body} role={nav === "data" ? "tabpanel" : undefined} className="page-body">
              <TabBody run={run} tab={backend} />
            </div>
          </>
        )}
      </div>
    </>
  );
}

/** What each column of the table holds, in plain words: its name, kind, the meaning the analysis worked out for it when it did, and how complete it is. Part of Your data. */
function ColumnMeanings({ run }: { run: RunResult }) {
  const cols = run.profile?.columns ?? [];
  if (!cols.length) return null;
  const KIND: Record<string, string> = { id: "identifier", categorical: "category", numeric: "number", date: "date", text: "free text", flag: "yes / no", other: "other" };
  const und = run.understanding?.columns ?? {};
  return (
    <section className="column-meanings" aria-label="How the data was set up">
      <h3>How the data was set up</h3>
      <p className="muted read">What each column holds, and how the analysis read it.</p>
      <div className="tablewrap">
        <table className="table">
          <thead><tr><th>Column</th><th>Kind</th><th>What it holds</th><th>Missing</th><th>Different values</th></tr></thead>
          <tbody>
            {cols.map((c) => (
              <tr key={c.name}>
                <td>{und[c.name]?.display_name ? <>{und[c.name].display_name} <span className="muted small">({c.name})</span></> : c.name}</td>
                <td>{KIND[c.role] ?? c.role}</td>
                <td>{und[c.name]?.meaning || <span className="muted">–</span>}</td>
                <td>{c.missing_pct ? `${c.missing_pct}%` : "none"}</td>
                <td>{c.unique.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
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
  const shown = (run.cleaning_log ?? []).map((s, i) => ({ s, i })).filter(({ i }) => !v?.isHidden(`cleaning:step:${i}`) && !v?.isFolded(`cleaning:step:${i}`));
  return (
    <>
    <TabViewBar tab="cleaning" />
    <ViewItem id="cleaning:steps" tag="section" className="view-section">
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
    </ViewItem>
    <ShowAllLink tab="cleaning" />
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
    <ViewItem id="plan:setup" tag="section" className="view-section">
    <p>
      <strong>Plan made by:</strong> {run.plan.source === "llm" || run.plan.source === "repaired" ? "the AI" : "our built-in rules"} · <strong>Number we focus on:</strong> {run.plan.target ?? "none"} ·{" "}
      <strong>Date used:</strong> {run.plan.date_col ?? "none"}
    </p>
    </ViewItem>
    <ViewItem id="plan:questions" tag="section" className="view-section">
    <h4>Questions we set out to answer</h4>
    <ul>{run.plan.questions.map((q, i) => !v?.isHidden(`plan:question:${i}`) && <li key={q} data-view-id={`plan:question:${i}`}>{q}</li>)}</ul>
    </ViewItem>
    <ViewItem id="plan:analyses" tag="section" className="view-section">
    <h4>What we checked, and why</h4>
    <p className="muted">Some checks were suggested by the AI. Others were added by code because they are always needed.</p>
    <ol>
      {run.plan.analyses.map((a, i) => !v?.isHidden(`plan:analysis:${i}`) && !v?.isFolded(`plan:analysis:${i}`) && (
        <li key={i} data-view-id={`plan:analysis:${i}`}>
          <strong>{a.tool.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())}</strong> {Object.values(a.params).flat().map(String).join(", ")}
          {a.origin && <span className="muted"> · <strong>{ORIGIN_LABEL[a.origin]}</strong></span>}
          <div className="muted">{a.why}</div>
        </li>
      ))}
    </ol>
    </ViewItem>
    <ShowAllLink tab="plan" />
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
  const vw = useView();
  const ranked = [...(run.findings ?? [])].filter((f) => f.insight).sort((a, b) => (b.insight!.score - a.insight!.score));
  return (
    <>
      {tab === "dashboard" && <BIDashboard run={run} />}

      {tab === "chat" && <><TabViewBar tab="chat" /><ViewItem id="chat:conversation" tag="section" className="view-section"><LensChat variant="tab" /></ViewItem></>}

      {tab === "data" && <><TabViewBar tab="data" />{!embedded && <ColumnMeanings run={run} />}<ViewItem id="data:table:clean" tag="section" className="view-section"><DataViewer runId={run.run_id} /></ViewItem></>}

      {tab === "report" && <ReportPage run={run} embedded={embedded} startChat={startChat} startPrint={startPrint} />}

      {tab === "insights" && (
        <div className="insights">
          {run.context?.company && <p className="muted">Tailored for <strong>{run.context.company}</strong>{run.context.explore ? " (exploratory run: no specific question was set)" : ""}.</p>}
          {run.report_view ? <Tiers view={run.report_view} goalAnswer={run.goal_answer} /> : ranked.map((f, i) => <InsightCard key={f.id} f={f} index={i + 1} />)}
          {!ranked.length && <p>No insights were produced.</p>}
        </div>
      )}

      {tab === "strategy" && !run.strategies && <p className="lead">No ideas to try were produced for this report.</p>}
      {tab === "strategy" && run.strategies && (
        <StrategyView strategies={run.report_view?.strategies.strategies ?? run.strategies} foundation={run.report_view?.strategies.foundation} dropped={run.report_view?.strategies.dropped ?? 0} summary={run.strategy_summary ?? ""} gaps={run.data_gaps ?? []} findings={run.findings ?? []} />
      )}

      {tab === "model" && run.data_model && <><TabViewBar tab="model" /><ViewItem id="model:links" tag="section" className="view-section"><DataModel model={run.data_model} /></ViewItem></>}
      {tab === "model" && !run.data_model && <p className="lead">This analysis used one table, so there are no files to connect.</p>}

      {tab === "cleaning" && <CleaningLog run={run} />}

      {tab === "plan" && run.plan && <PlanView run={run} />}

      {tab === "powerbi" && run.powerbi && (
        <div className="powerbi">
          <TabViewBar tab="powerbi" />
          <ViewItem id="powerbi:pack" tag="section" className="view-section">
          <p>
            The pack contains <code>{run.powerbi.files.join("</code>, <code>")}</code>. Open <code>POWERBI_GUIDE.md</code> first: load the CSV in Power BI Desktop, paste the measures, apply the theme, then build the visuals below.
          </p>
          <h4>DAX measures</h4>
          <pre>{run.powerbi.measures.filter((_, i) => !vw?.isHidden(`powerbi:measure:${i}`) && !vw?.isFolded(`powerbi:measure:${i}`)).join("\n\n")}</pre>
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
          </ViewItem>
          <ShowAllLink tab="powerbi" />
        </div>
      )}
    </>
  );
}
