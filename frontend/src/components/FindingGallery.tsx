import { useState } from "react";
import { api, DashTile, Finding, RunResult, isDemo } from "../api";
import { humanise } from "../display";
import FindingTable from "./FindingTable";
import InsightCard from "./InsightCard";
import { ShowMore, ViewImg, ViewItem, ViewTitle } from "./view/ViewUI";

const LEVEL: Record<string, string> = { high: "tag tag-ok", medium: "tag tag-mid", low: "tag tag-warn", exploratory: "tag tag-info", descriptive: "tag" };

export function Tile({ f, onOpen, und, items }: { f: Finding; onOpen: () => void; und?: RunResult["understanding"]; items?: { measure: string; text: string }[] }) {
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
      {items && items.length > 1 && <ul className="tile-measures small">{items.map((i) => <li key={i.measure + i.text}>{i.text}</li>)}</ul>}
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

/** Below the dashboard grid: the answer to the goal in words, and "More charts" for everything the selection kept off the screen.
 *  Nothing here is drawn until opened, so a long list costs nothing. The charts on the dashboard itself are chosen by the saved selection (dashplan.py). */
export default function FindingGallery({ run, more, extra }: { run: RunResult; more: DashTile[]; extra?: React.ReactNode }) {
  const [open, setOpen] = useState<Finding | null>(null);
  const [busy, setBusy] = useState("");
  const [showMore, setShowMore] = useState(false);
  const all = (run.findings ?? []).filter((f) => f.insight);
  const ans = run.goal_answer;
  const moreFindings = more.filter((t) => t.kind === "finding").map((t) => ({ t, f: all.find((x) => x.id === t.finding_id) })).filter((x): x is { t: DashTile; f: Finding } => !!x.f);
  const total = moreFindings.length + (more.length - moreFindings.length) + (extra ? 1 : 0);
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
        </section>
      )}

      {total > 0 && (
        <details className="dash-section more-charts" onToggle={(e) => setShowMore((e.currentTarget as HTMLDetailsElement).open)}>
          <summary><strong>More charts</strong> <span className="muted">· {moreFindings.length + more.filter((t) => t.kind !== "finding").length + (extra ? 1 : 0)} ranked lower</span></summary>
          {showMore && (
            <>
              {extra}
              <div className="tile-grid"><ShowMore tab="dashboard" items={moreFindings.map(({ t, f }) => ({ id: `dashboard:chart:${f.id}`, node: <Tile key={f.id} f={f} items={t.items} und={run.understanding} onOpen={() => setOpen(f)} /> }))} /></div>
            </>
          )}
        </details>
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
