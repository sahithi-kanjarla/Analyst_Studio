import { useState } from "react";
import { api, Finding, RunResult, isDemo } from "../api";
import { humanise } from "../display";
import FindingTable from "./FindingTable";
import InsightCard from "./InsightCard";
import { ShowMore, ViewImg, ViewItem, ViewTitle } from "./view/ViewUI";

const TOP = 4;
const LEVEL: Record<string, string> = { high: "tag tag-ok", medium: "tag tag-mid", low: "tag tag-warn", exploratory: "tag tag-info", descriptive: "tag" };

function Tile({ f, onOpen, und }: { f: Finding; onOpen: () => void; und?: RunResult["understanding"] }) {
  const ins = { ...f.insight!, headline: humanise(f.insight!.headline, und) };
  const id = `dashboard:chart:${f.id}`;
  return (
    <ViewItem id={id} tag="article" className="tile">
      <header>
        <ViewTitle id={id} tag="h4" text={ins.headline} />
        <div className="meta">
          <span className={LEVEL[ins.confidence.level] ?? "tag"}>{ins.confidence.label}</span>
          {f.chart_type && <span className="muted" title={f.chart_why}>{f.chart_type}</span>}
        </div>
      </header>
      {f.chart_url ? (
        <button className="tile-img" onClick={onOpen} aria-label={`Open details: ${ins.headline}`}>
          <ViewImg id={id} src={f.chart_url} alt={humanise(f.title, und)} />
        </button>
      ) : (
        <FindingTable f={f} />
      )}
      <footer>
        <button className="link" onClick={onOpen}>Details, trust and next steps →</button>
      </footer>
    </ViewItem>
  );
}

/** Under the interactive dashboard: the answer to the goal, the highest-value charts first (by insight score), everything else under "More charts". */
export default function FindingGallery({ run }: { run: RunResult }) {
  const [open, setOpen] = useState<Finding | null>(null);
  const [busy, setBusy] = useState("");
  const all = (run.findings ?? []).filter((f) => f.insight);
  const ans = run.goal_answer;
  const answerIds = new Set(ans?.finding_ids ?? []);
  const answerCharts = all.filter((f) => answerIds.has(f.id) && f.chart_url).sort((a) => (a.tool === "segment_trend" ? -1 : 1));
  const shownInAnswer = new Set(answerCharts.map((f) => f.id));
  const order = run.report_view?.gallery;       // goal-named findings first; a small or negligible effect, or a weak correlation, is never a key finding
  const byScore = (a: Finding, b: Finding) => b.insight!.score - a.insight!.score;
  const rest = all.filter((f) => !shownInAnswer.has(f.id));
  const keyIds = order ? order.key.filter((id) => !shownInAnswer.has(id)) : [];
  const top = order ? keyIds.map((id) => rest.find((f) => f.id === id)).filter((f): f is Finding => !!f) : rest.sort(byScore).slice(0, TOP);
  const more = order ? rest.filter((f) => !keyIds.includes(f.id)).sort(byScore) : rest.slice(TOP);
  const dm = run.derived_metrics;

  const remove = async (name: string) => {
    setBusy(name);
    try {
      const r = await api.removeDerived(run.run_id, [name]);
      window.location.hash = `#/run/${r.run_id}`;
    } catch (e) {
      setBusy("");
      alert((e as Error).message);
    }
  };

  return (
    <div className="gallery">
      {ans && (
        <section className="callout goal-answer">
          <strong>Answer to your goal:</strong> {ans.sentence} {ans.groups_sentence}
          {answerCharts.length > 0 && (
            <div className="tile-grid" style={{ marginTop: 12 }}>
              <ShowMore tab="dashboard" items={answerCharts.map((f) => ({ id: `dashboard:chart:${f.id}`, node: <Tile key={f.id} f={f} und={run.understanding} onOpen={() => setOpen(f)} /> }))} />
            </div>
          )}
        </section>
      )}

      <section className="dash-section">
        <h4>Key findings <span className="muted">· highest value first</span></h4>
        <div className="tile-grid"><ShowMore tab="dashboard" items={top.map((f) => ({ id: `dashboard:chart:${f.id}`, node: <Tile key={f.id} f={f} und={run.understanding} onOpen={() => setOpen(f)} /> }))} /></div>
      </section>

      {more.length > 0 && (
        <section className="dash-section more-charts">
          <h4>More charts <span className="muted">· {more.length}</span></h4>
          <div className="tile-grid"><ShowMore tab="dashboard" items={more.map((f) => ({ id: `dashboard:chart:${f.id}`, node: <Tile key={f.id} f={f} und={run.understanding} onOpen={() => setOpen(f)} /> }))} /></div>
        </section>
      )}

      {dm && (dm.created.length > 0 || dm.rejected.length > 0) && (
        <section className="dash-section derived-box">
          <h4>Definitions used: derived metrics</h4>
          {dm.created.map((d) => (
            <p key={d.name} className="small">
              <code>{d.name}</code> = {d.a} / {d.b} (ratio of sums). {d.meaning}{" "}
              {!isDemo(run.run_id) && <button className="link" disabled={!!busy} onClick={() => remove(d.name)}>{busy === d.name ? "Re-running…" : "Remove and re-run"}</button>}
            </p>
          ))}
          {dm.rejected.map((r) => <p key={r.a + r.b} className="muted small">Not created: {r.a} / {r.b}. {r.reason}.</p>)}
        </section>
      )}

      {open && (
        <div className="modal" role="dialog" aria-modal="true" aria-label="Insight details" onClick={() => setOpen(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <button className="modal-x" aria-label="Close" onClick={() => setOpen(null)}>✕</button>
            <InsightCard f={open} index={all.indexOf(open) + 1} />
            {open.chart_why && <p className="muted small"><strong>Why this chart:</strong> {open.chart_why}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
