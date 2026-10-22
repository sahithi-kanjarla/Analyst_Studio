import { useMemo, useState } from "react";
import { Finding, RunResult } from "../api";
import FindingTable from "./FindingTable";
import InsightCard from "./InsightCard";

const SECTIONS: { id: string; title: string; blurb: string; tools: string[] }[] = [
  { id: "drivers", title: "What drives the result", blurb: "Differences between groups and relationships between columns", tools: ["group_compare", "outcome_compare", "scatter", "heatmap", "composition", "correlation", "robust_compare"] },
  { id: "time", title: "Over time", blurb: "Trends and repeating patterns", tools: ["trend", "segment_trend", "seasonality"] },
  { id: "shape", title: "Shape of the data", blurb: "Distributions, concentration and unusual values", tools: ["distribution", "pareto", "outliers", "category_share"] },
  { id: "tables", title: "Tables", blurb: "Exact figures to quote and check", tools: ["summary_table", "group_table"] },
];

const LEVEL: Record<string, string> = { high: "tag tag-ok", medium: "tag tag-mid", low: "tag tag-warn", exploratory: "tag tag-info", descriptive: "tag" };

function Tile({ f, big, onOpen }: { f: Finding; big?: boolean; onOpen: () => void }) {
  const ins = f.insight!;
  return (
    <article className={`tile ${big ? "tile-big" : ""}`}>
      <header>
        <h4>{ins.headline}</h4>
        <div className="meta">
          <span className={LEVEL[ins.confidence.level] ?? "tag"}>{ins.confidence.label}</span>
          {ins.kpi_related && <span className="tag tag-kpi">your KPI</span>}
          {f.chart_type && <span className="muted" title={f.chart_why}>{f.chart_type}</span>}
        </div>
      </header>
      {f.chart_url ? (
        <button className="tile-img" onClick={onOpen} aria-label={`Open details: ${ins.headline}`}>
          <img src={f.chart_url} alt={f.title} loading="lazy" />
        </button>
      ) : (
        <FindingTable f={f} />
      )}
      <footer>
        <button className="link" onClick={onOpen}>Details, trust and next steps →</button>
      </footer>
    </article>
  );
}

/** Power BI-style overview: headline numbers, then every chart arranged by what question it answers. */
export default function Dashboard({ run }: { run: RunResult }) {
  const [open, setOpen] = useState<Finding | null>(null);
  const [only, setOnly] = useState<"all" | "kpi" | "high">("all");
  const findings = useMemo(() => (run.findings ?? []).filter((f) => f.insight), [run.findings]);
  const visible = findings.filter((f) => only === "all" || (only === "kpi" ? f.insight!.kpi_related : f.insight!.confidence.level === "high"));
  const hero = [...findings].filter((f) => f.chart_url && f.insight!.confidence.level !== "descriptive").sort((a, b) => b.insight!.score - a.insight!.score).slice(0, 2);
  const heroIds = new Set(hero.map((f) => f.id));
  const topStrategy = run.strategies?.find((s) => s.kind === "data");

  return (
    <div className="dashboard">
      <div className="dash-head">
        <div>
          <h3>{run.context?.company ? `${run.context.company}: overview` : "Overview"}</h3>
          <p className="muted">{run.plan?.objective}</p>
        </div>
        <div className="seg" role="group" aria-label="Filter charts">
          <button className={only === "all" ? "seg-on" : ""} onClick={() => setOnly("all")}>All</button>
          <button className={only === "high" ? "seg-on" : ""} onClick={() => setOnly("high")}>High confidence</button>
          {!!run.context?.kpis?.length && <button className={only === "kpi" ? "seg-on" : ""} onClick={() => setOnly("kpi")}>My KPIs</button>}
        </div>
      </div>

      {topStrategy && (
        <div className="callout">
          <strong>Top recommendation:</strong> {topStrategy.title}. <span className="muted">{topStrategy.objective}</span>
        </div>
      )}

      {only === "all" && hero.length > 0 && (
        <div className="hero-row">
          {hero.map((f) => <Tile key={f.id} f={f} big onOpen={() => setOpen(f)} />)}
        </div>
      )}

      {SECTIONS.map((s) => {
        const items = visible.filter((f) => s.tools.includes(f.tool) && !(only === "all" && heroIds.has(f.id)));
        if (!items.length) return null;
        return (
          <section key={s.id} className="dash-section">
            <h4>{s.title} <span className="muted">· {s.blurb}</span></h4>
            <div className="tile-grid">
              {items.map((f) => <Tile key={f.id} f={f} onOpen={() => setOpen(f)} />)}
            </div>
          </section>
        );
      })}
      {visible.length === 0 && <p>Nothing matches this filter.</p>}

      {open && (
        <div className="modal" role="dialog" aria-modal="true" aria-label="Insight details" onClick={() => setOpen(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <button className="modal-x" aria-label="Close" onClick={() => setOpen(null)}>✕</button>
            <InsightCard f={open} index={findings.indexOf(open) + 1} />
            {open.chart_why && <p className="muted small"><strong>Why this chart:</strong> {open.chart_why}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
