import { DataGap, Finding, Strategy } from "../api";
import State from "./State";
import { humanise, Understanding } from "../display";
import { ShowAllLink, ShowMore, TabViewBar, ViewItem, ViewTitle } from "./view/ViewUI";
import { useView } from "./view/ViewContext";

const LEVEL: Record<string, string> = { High: "lvl lvl-high", Medium: "lvl lvl-mid", Low: "lvl lvl-low" };

/** One line of evidence, short enough to read at a glance; the whole sentence stays in the title and in "The full plan". */
function clip(text: string, n = 110): string {
  const t = text.trim();
  return t.length > n ? t.slice(0, n - 1).replace(/[\s,;:]+\S*$/, "") + "…" : t;
}

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
                <span key={s.id} className="mx-pill" title={`${s.rank}. ${s.title}`}><b>{s.rank}</b> <span className="mx-pill-name">{clip(s.title, 26)}</span></span>
              ))}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

export default function StrategyView({ strategies, summary, gaps, findings, understanding, foundation = [], dropped = 0, more = [] }: { strategies: Strategy[]; summary: string; gaps: DataGap[]; findings: Finding[]; understanding?: Understanding | null; foundation?: { id: string; line: string }[]; dropped?: number; more?: Strategy[] }) {
  const view = useView();
  const short = !!view?.isShort("strategy");
  const backed = strategies.filter((s) => s.kind === "data");
  const say = (t: string) => humanise(t, understanding);
  const headline = (id: string) => say(findings.find((f) => f.id === id)?.insight?.headline ?? id);
  const quickWins = strategies.filter((s) => s.impact === "High" && s.effort === "Low").length;
  const fromData = backed.length;

  if (!strategies.length) {
    return (
      <div className="strategy">
        <TabViewBar tab="strategy" />
        <State kind="empty" title="No ideas to try yet">That usually means no finding was strong enough to act on. The Insights tab shows what was checked.</State>
      </div>
    );
  }

  return (
    <div className="strategy">
      <TabViewBar tab="strategy" />
      {summary && <p className="lead">{say(summary)}</p>}

      <section className="strat-stats" aria-label="These ideas at a glance">
        <div className="rp-tile" data-kind="numbers"><span className="rp-tile-value">{strategies.length}</span><span className="rp-tile-label">{strategies.length === 1 ? "Idea to try" : "Ideas to try"}</span></div>
        <div className="rp-tile" data-kind="numbers"><span className="rp-tile-value">{quickWins}</span><span className="rp-tile-label">Quick {quickWins === 1 ? "win" : "wins"} (high impact, low effort)</span></div>
        <div className="rp-tile" data-kind="numbers"><span className="rp-tile-value">{fromData}</span><span className="rp-tile-label">Backed by a finding in your data</span></div>
      </section>

      {backed.length > 0 && (
        <div className="matrix-wrap">
          <h4>Where each idea sits</h4>
          <Matrix items={strategies} />
          <p className="muted small">Top-left is the best place to start: most to gain, least to do.</p>
        </div>
      )}

      <div className="strat-grid">
        <ShowMore tab="strategy" items={strategies.map((s) => {
                    return { id: `strategy:${s.id}`, node: (
            <ViewItem key={s.id} id={`strategy:${s.id}`} tag="article" className={`strat ${s.kind === "foundation" ? "strat-foundation" : ""}`} data-kind="idea">
              <header>
                <span className="insight-num">{s.rank}</span>
                <div>
                  <ViewTitle id={`strategy:${s.id}`} tag="h4" text={say(s.title)} />
                  <div className="meta">
                    <span className={LEVEL[s.impact]}>Impact: {s.impact}</span>
                    <span className={LEVEL[s.effort === "Low" ? "High" : s.effort === "High" ? "Low" : "Medium"]}>Effort: {s.effort}</span>
                    <span className="tag">{s.horizon}</span>
                    {s.confidence && <span className="tag">Confidence: {s.confidence}</span>}
                    {s.kind === "foundation" && <span className="tag tag-info">groundwork, not from a finding</span>}
                    {s.source === "llm" && <span className="tag tag-info">AI-tailored</span>}
                  </div>
                </div>
              </header>

              {!short && s.objective && <p className="strat-why" data-kind="reason">{say(s.objective)}</p>}

              {!short && s.evidence.length > 0 && (
                <p className="strat-evidence-num" data-kind="evidence" title={headline(s.evidence[0])}>
                  <span className="muted">Based on</span> {clip(headline(s.evidence[0]), 130)}
                </p>
              )}

              {!short && s.actions.length > 0 && (
                <div className="strat-steps" data-kind="first-step">
                  <h5>First steps</h5>
                  <ol className="tight">{s.actions.map((a, i) => <li key={i}>{say(a)}</li>)}</ol>
                </div>
              )}

              {!short && s.how_to_test && <p className="strat-test"><strong>Test it first:</strong> {say(s.how_to_test)}</p>}

              {!short && s.kpis.length > 0 && (
                <div className="strat-kpis" data-kind="kpis">
                  <h5>What to track</h5>
                  <ul className="chip-list">{s.kpis.map((k, i) => <li key={i} className="chip-static">{say(k)}</li>)}</ul>
                </div>
              )}

              {!short && (s.risks.length > 0 || s.evidence.length > 1) && (
                <details className="fold fold-working">
                  <summary>How we got this<span className="fold-hint">every finding it rests on and what could go wrong</span></summary>
                  {s.evidence.length > 1 && <><h5>Findings behind it</h5><ul className="tight">{s.evidence.map((id) => <li key={id}>{headline(id)}</li>)}</ul></>}
                  {s.risks.length > 0 && <><h5>What could go wrong</h5><ul className="tight">{s.risks.map((r, i) => <li key={i}>{say(r)}</li>)}</ul></>}
                </details>
              )}
            </ViewItem>) };
        })} />
      </div>
      <ShowAllLink tab="strategy" />

      {more.length > 0 && (
        <details className="fold fold-more">
          <summary>More ({more.length} {more.length === 1 ? "idea" : "ideas"} that ranked lower)</summary>
          <ul className="tight">
            {more.map((s) => (
              <ViewItem key={s.id} id={`strategy:${s.id}`} tag="li" data-kind="idea">
                <strong>{say(s.title)}</strong>{s.objective ? ` — ${clip(say(s.objective), 130)}` : ""}
              </ViewItem>
            ))}
          </ul>
        </details>
      )}

      {dropped > 0 && <p className="muted small">{dropped} lower-ranked {dropped === 1 ? "idea is" : "ideas are"} not shown.</p>}
      {foundation.length > 0 && (
        <section className="strat-foundations">
          <h4>Groundwork worth doing anyway</h4>
          <ul className="tight">{foundation.map((f) => <li key={f.id}>{say(f.line)}</li>)}</ul>
        </section>
      )}

      {gaps.length > 0 && (
        <section className="gaps">
          <h4>What would sharpen this</h4>
          <ul>
            {gaps.map((g, i) => (
              <li key={i} className={g.severity === "warn" ? "warn-text" : ""}>
                {g.severity === "warn" ? "⚠ " : ""}
                <strong>{say(g.title)}.</strong> {say(g.detail)} <em>{say(g.suggestion)}</em>
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="muted small">These are ideas to test with a small pilot first: the analysis shows things moving together, not one causing the other.</p>
    </div>
  );
}
