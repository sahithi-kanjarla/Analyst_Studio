import { useState } from "react";
import { api, DashTile, Finding, RunResult, isDemo } from "../api";
import { humanise, plainTitle } from "../display";
import { moreChartCount } from "../morecharts";
import FindingTable from "./FindingTable";
import ChartModal from "./ChartModal";
import InsightCard from "./InsightCard";
import { ShowMore, ViewImg, ViewItem, ViewTitle } from "./view/ViewUI";

const LEVEL: Record<string, string> = { high: "tag tag-ok", medium: "tag tag-mid", low: "tag tag-warn", exploratory: "tag tag-info", descriptive: "tag" };

export function Tile({ f, onOpen, und, items }: { f: Finding; onOpen: () => void; und?: RunResult["understanding"]; items?: { measure: string; text: string }[] }) {
  const ins = { ...f.insight!, headline: humanise(f.insight!.headline, und) };
  const id = `dashboard:chart:${f.id}`;
  const title = plainTitle(f.title, und);
  return (
    <ViewItem id={id} tag="article" className="tile tile-compact">
      <header>
        <ViewTitle id={id} tag="h4" text={title} />
        <span className={LEVEL[ins.confidence.level] ?? "tag"}>{ins.confidence.label}</span>
      </header>
      <p className="tile-take">{ins.headline}</p>
      {items && items.length > 1 && <ul className="tile-measures small">{items.map((i) => <li key={i.measure + i.text}>{i.text}</li>)}</ul>}
      {f.chart_url ? (
        <button className="tile-img" onClick={onOpen} aria-label={`Open full size: ${title}`}>
          <ViewImg id={id} src={f.chart_url} alt={title} />
        </button>
      ) : (
        <button className="tile-img tile-table" onClick={onOpen} aria-label={`Open full size: ${title}`}><FindingTable f={f} /></button>
      )}
    </ViewItem>
  );
}

/** The finding's full-size content: the whole insight card (chart, what we see, trust, next steps) and why this chart was drawn. */
export function FindingZoom({ f, index }: { f: Finding; index: number }) {
  return (
    <>
      <InsightCard f={f} index={index} />
      {f.chart_why && <p className="muted small"><strong>Why this chart:</strong> {f.chart_why}</p>}
    </>
  );
}

/** Below the dashboard grid: the answer to the goal in words, and "More charts" for everything the selection kept off the screen.
 *  Nothing here is drawn until opened, so a long list costs nothing. The charts on the dashboard itself are chosen by the saved selection (dashplan.py). */
export default function FindingGallery({ run, more, extra, extraCount = 0 }: { run: RunResult; more: DashTile[]; extra?: React.ReactNode; extraCount?: number }) {
  const [open, setOpen] = useState<Finding | null>(null);
  const [busy, setBusy] = useState("");
  const [showMore, setShowMore] = useState(false);
  const all = (run.findings ?? []).filter((f) => f.insight);
  const ans = run.goal_answer;
  const moreFindings = more.filter((t) => t.kind === "finding").map((t) => ({ t, f: all.find((x) => x.id === t.finding_id) })).filter((x): x is { t: DashTile; f: Finding } => !!x.f);
  const total = moreChartCount(moreFindings.length, extraCount);
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

      {(total > 0 || !!extra) && (
        <details className="dash-section more-charts" onToggle={(e) => setShowMore((e.currentTarget as HTMLDetailsElement).open)}>
          <summary><strong>More charts</strong> <span className="muted">· {total > 0 ? `${total} ranked lower` : "the full table"}</span></summary>
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
        <ChartModal current={open.id} onClose={() => setOpen(null)}
          onSelect={(id) => setOpen(moreFindings.find((x) => x.f.id === id)?.f ?? null)}
          entries={moreFindings.map(({ f }) => ({ id: f.id, node: <FindingZoom f={f} index={all.indexOf(f) + 1} /> }))} />
      )}
    </div>
  );
}
