import { DataGap, Finding, Strategy } from "../api";
import { ShowMore, TabViewBar, ViewItem, ViewTitle } from "./view/ViewUI";
import { useView } from "./view/ViewContext";

const LEVEL: Record<string, string> = { High: "lvl lvl-high", Medium: "lvl lvl-mid", Low: "lvl lvl-low" };

function Matrix({ items }: { items: Strategy[] }) {
  // Impact (rows, high at top) against effort (columns, low at left): the top-left cell holds the best candidates.
  const cell = (impact: string, effort: string) => items.filter((s) => s.impact === impact && s.effort === effort);
  return (
    <div className="matrix" role="table" aria-label="Impact versus effort">
      <div className="mx-corner" />
      {["Low", "Medium", "High"].map((e) => (
        <div key={e} className="mx-head">{e} effort</div>
      ))}
      {["High", "Medium", "Low"].map((imp) => (
        <div className="mx-row" key={imp}>
          <div className="mx-side">{imp} impact</div>
          {["Low", "Medium", "High"].map((eff) => (
            <div key={eff} className={`mx-cell ${imp === "High" && eff === "Low" ? "mx-best" : ""}`}>
              {cell(imp, eff).map((s) => (
                <span key={s.id} className="mx-pill" title={s.title}>{s.rank}</span>
              ))}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

export default function StrategyView({ strategies, summary, gaps, findings, foundation = [], dropped = 0 }: { strategies: Strategy[]; summary: string; gaps: DataGap[]; findings: Finding[]; foundation?: { id: string; line: string }[]; dropped?: number }) {
  const view = useView();
  const short = !!view?.isShort("strategy");
  const backed = strategies.filter((s) => s.kind === "data");
  const headline = (id: string) => findings.find((f) => f.id === id)?.insight?.headline ?? id;
  return (
    <div className="strategy">
      <TabViewBar tab="strategy" />
      <p className="lead">{summary}</p>

      {backed.length > 0 && (
        <div className="matrix-wrap">
          <h4>Where each strategy sits</h4>
          <Matrix items={strategies} />
          <p className="muted small">Numbers are the strategy ranks below. Top-left (high impact, low effort) first.</p>
        </div>
      )}

      <ShowMore tab="strategy" items={strategies.map((s) => ({ id: `strategy:${s.id}`, node: (
        <ViewItem key={s.id} id={`strategy:${s.id}`} tag="article" className={`strat ${s.kind === "foundation" ? "strat-foundation" : ""}`}>
          <header>
            <span className="insight-num">{s.rank}</span>
            <div>
              <ViewTitle id={`strategy:${s.id}`} tag="h4" text={s.title} />
              <div className="meta">
                <span className={LEVEL[s.impact]}>Impact: {s.impact}</span>
                <span className={LEVEL[s.effort === "Low" ? "High" : s.effort === "High" ? "Low" : "Medium"]}>Effort: {s.effort}</span>
                <span className="tag">{s.horizon}</span>
                {s.confidence && <span className="tag">Confidence: {s.confidence}</span>}
                {s.kind === "foundation" && <span className="tag tag-info">foundation, not from a finding</span>}
                {s.source === "llm" && <span className="tag tag-info">AI-tailored</span>}
              </div>
            </div>
          </header>
          {!short && <p>{s.objective}</p>}
          {!short && s.evidence.length > 0 && (
            <p className="muted small">
              <strong>Based on:</strong> {s.evidence.map((id) => `${headline(id)} (${id})`).join("; ")}
            </p>
          )}
          {!short && <div className="strat-cols">
            <section>
              <h5>Actions</h5>
              <ol className="tight">
                {s.actions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ol>
            </section>
            <section>
              <h5>Test it first</h5>
              <p>{s.how_to_test}</p>
              <h5>KPIs to track</h5>
              <ul className="tight">
                {s.kpis.map((k, i) => (
                  <li key={i}>{k}</li>
                ))}
              </ul>
              <h5>Risks</h5>
              <ul className="tight">
                {s.risks.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </section>
          </div>}
        </ViewItem>) }))} />

      {dropped > 0 && <p className="muted small">{dropped} lower-ranked {dropped === 1 ? "strategy is" : "strategies are"} not shown.</p>}
      {foundation.length > 0 && (
        <section>
          <h4>Foundations</h4>
          <ul className="tight">{foundation.map((f) => <li key={f.id}>{f.line}</li>)}</ul>
        </section>
      )}

      {gaps.length > 0 && (
        <section className="gaps">
          <h4>Data gaps and what would sharpen this</h4>
          <ul>
            {gaps.map((g, i) => (
              <li key={i} className={g.severity === "warn" ? "warn-text" : ""}>
                {g.severity === "warn" ? "⚠ " : ""}
                <strong>{g.title}.</strong> {g.detail} <em>{g.suggestion}</em>
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="muted small">Strategies are hypotheses to test with a small pilot first: the analysis shows association, not cause and effect.</p>
    </div>
  );
}
