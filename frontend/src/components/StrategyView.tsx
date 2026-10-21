import { DataGap, Finding, Strategy } from "../api";

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

export default function StrategyView({ strategies, summary, gaps, findings }: { strategies: Strategy[]; summary: string; gaps: DataGap[]; findings: Finding[] }) {
  const backed = strategies.filter((s) => s.kind === "data");
  const headline = (id: string) => findings.find((f) => f.id === id)?.insight?.headline ?? id;
  return (
    <div className="strategy">
      <p className="lead">{summary}</p>

      {backed.length > 0 && (
        <div className="matrix-wrap">
          <h4>Where each strategy sits</h4>
          <Matrix items={strategies} />
          <p className="muted small">Numbers are the strategy ranks below. Top-left (high impact, low effort) first.</p>
        </div>
      )}

      {strategies.map((s) => (
        <article className={`strat ${s.kind === "foundation" ? "strat-foundation" : ""}`} key={s.id}>
          <header>
            <span className="insight-num">{s.rank}</span>
            <div>
              <h4>{s.title}</h4>
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
          <p>{s.objective}</p>
          {s.evidence.length > 0 && (
            <p className="muted small">
              <strong>Based on:</strong> {s.evidence.map(headline).join("; ")}
            </p>
          )}
          <div className="strat-cols">
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
          </div>
        </article>
      ))}

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
