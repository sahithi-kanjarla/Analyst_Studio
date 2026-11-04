import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { CardTable, ReportCard as Card } from "../api";
import { planTable, TABLE_ROWS } from "../chartsize";
import { humanise, plainTitle, prettify, Understanding } from "../display";
import { ViewImg, ViewItem, ViewTitle } from "./view/ViewUI";

export const LEVEL_CLASS: Record<string, string> = {
  high: "tag tag-ok",
  medium: "tag tag-mid",
  low: "tag tag-warn",
  exploratory: "tag tag-info",
  descriptive: "tag",
};

/** What kind of finding this is, in the reader's words, taken from the analysis that produced it. Never from a column name. */
const KIND: Record<string, string> = {
  trend: "Trend",
  seasonality: "Seasonal pattern",
  group_compare: "Ranking",
  rate_compare: "Ranking",
  ranking: "Ranking",
  group_table: "Ranking",
  outliers: "Unusual values",
  distribution: "Spread",
  composition: "Mix",
  correlation: "Moves together",
  scatter: "Moves together",
  chi_square: "Linked",
  concentration: "Concentration",
  growth: "Change over time",
};
export const kindOf = (tool: string) => KIND[tool] ?? (tool ? prettify(tool) : "");

/** The table under a finding: a few rows in the chart's own order, the rest behind "Show all (n)". It is never hidden behind an arrow. */
export function Table({ t, und }: { t: CardTable; und?: Understanding | null }) {
  const [all, setAll] = useState(false);
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const num = (v: unknown) => { const n = parseFloat(String(v).replace(/[,%\s]/g, "")); return Number.isFinite(n) ? n : null; };
  // sorting is display only: the rows are the same, in another order, and the header says which way
  const ordered = !sort ? t.rows : [...t.rows].sort((a, b) => {
    const x = num(a[sort.col]), y = num(b[sort.col]);
    return (x !== null && y !== null ? x - y : String(a[sort.col]).localeCompare(String(b[sort.col]))) * sort.dir;
  });
  const plan = planTable(ordered, { all });
  return (
    <section className="key-table" data-kind="table">
      <h5>{humanise(t.title, und)}</h5>
      <div className="tablewrap">
        <table className="table">
          <thead>
            <tr>{t.columns.map((c, j) => (
              <th key={c} aria-sort={sort?.col === j ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
                <button type="button" className="th-sort" onClick={() => setSort(sort?.col === j ? (sort.dir === 1 ? { col: j, dir: -1 } : null) : { col: j, dir: j === 0 ? 1 : -1 })}>
                  {humanise(c, und)}{sort?.col === j ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                </button>
              </th>
            ))}</tr>
          </thead>
          <tbody>
            {plan.items.map((r, i) => (
              <tr key={i}>{r.map((v, j) => <td key={j} className={j > 0 ? "num" : ""}>{String(v)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        {plan.showAll || all ? (
          <button type="button" className="chip no-print" aria-expanded={all} onClick={() => setAll(!all)}>
            {all ? `Show ${TABLE_ROWS}` : `Show all (${t.rows.length})`}
          </button>
        ) : null}
        {t.total > t.rows.length && <> {plan.subtitle} of the {t.total.toLocaleString()} in your data; the Data tab shows every row.</>}
      </p>
    </section>
  );
}

/** The working behind one finding, in the one fold named "How we got this": how far to trust it, the checks, and what to look at next. */
export function hasWorking(c: Card): boolean {
  const st = c.stats;
  return c.confidence.reasons.length > 0 || c.checks.length > 0 || c.hypotheses.length > 0 || c.check_next.length > 0 || st.n != null || st.p != null;
}
export function Working({ c, say, children }: { c: Card; say: (t: string) => string; children?: React.ReactNode }) {
  const st = c.stats;
  return (
            <details className="fold fold-working">
              
              <summary>How we got this<span className="fold-hint">how far to trust it, the checks we ran and what to look at next</span></summary>
              {children}
              <ul className="tight">
                {c.confidence.reasons.map((r, i) => <li key={i}>{say(r)}</li>)}
                {c.checks.map((x, i) => <li key={`c${i}`}>{say(x)}</li>)}
                {c.also.flatMap((a) => a.checks).map((x, i) => <li key={`a${i}`}>{say(x)}</li>)}
              </ul>
              {c.hypotheses.length > 0 && (
                <>
                  <h5>Possible explanations to test <span className="muted">(not conclusions)</span></h5>
                  <ul className="tight">{c.hypotheses.map((h, i) => <li key={i}>{say(h)}</li>)}</ul>
                </>
              )}
              {c.check_next.length > 0 && (
                <>
                  <h5>Check next</h5>
                  <ul className="tight">{c.check_next.map((x, i) => <li key={i}>{say(x)}</li>)}</ul>
                </>
              )}
              <p className="stats">
                {st.n != null && <>n = {String(st.n)} </>}
                {st.p != null && <>· {st.p < 0.001 ? "p < 0.001" : `p = ${st.p.toPrecision(2)}`} </>}
                {st.p_adjusted != null && <>· adjusted {st.p_adjusted < 0.001 ? "p < 0.001" : `p = ${st.p_adjusted.toPrecision(2)}`}</>}
              </p>
            </details>
  );
}

/** One finding: what kind it is and how far to trust it as badges, its chart, what it says in one or two sentences,
 *  its per-group figures, and the working folded into one clearly named section. */
export default function ReportCard({ c, index, compact = false, understanding }: { c: Card; index: number; compact?: boolean; understanding?: Understanding | null }) {
  const und = understanding;
  const say = (t: string) => humanise(t, und);
  const kind = kindOf(c.tool);
  const working = hasWorking(c);
  return (
    <article className="insight" data-kind="insight">
      <header className="insight-head">
        <span className="insight-num">{index}</span>
        <div className="insight-title">
          <ViewTitle id={`insights:card:${c.id}`} tag="h4" text={plainTitle(c.title, und)} />
          <p className="iw-line">{say(c.headline)}</p>
          <div className="meta">
            {kind && <span className="tag tag-kind">{kind}</span>}
            <span className={LEVEL_CLASS[c.confidence.level] ?? "tag"} title={c.confidence.reasons.join(" ")}>{c.confidence.label} confidence</span>
            {c.kpi_related && <span className="tag tag-kpi">your KPI</span>}
          </div>
        </div>
      </header>

      <div className="insight-body">
        {c.chart_url && <ViewItem id={`insights:chart:${c.id}`} className="insight-chart" data-kind="chart"><ViewImg id={`insights:chart:${c.id}`} src={c.chart_url} alt={say(c.title)} /></ViewItem>}
        {!compact && <div className="insight-text">
          <section data-kind="takeaway">
            <h5>What we see</h5>
            <ReactMarkdown>{say(c.observation)}</ReactMarkdown>
          </section>
          {c.why_it_matters && (
            <section data-kind="reason">
              <h5>Why it matters</h5>
              <p>{say(c.why_it_matters)}</p>
            </section>
          )}
          {c.also.map((a) => (
            <section key={a.id}>
              <h5>Same split, {say(a.target)}</h5>
              <p><strong>{say(a.headline)}.</strong></p>
              <ReactMarkdown>{say(a.observation)}</ReactMarkdown>
            </section>
          ))}
          {c.merged && c.merged.items.length > 1 && (
            <section>
              <h5>Same leader on {c.merged.items.length} measures</h5>
              <ul>{c.merged.items.map((it) => <li key={it.id}>{say(it.text)}</li>)}</ul>
            </section>
          )}
          {[...c.tables, ...c.also.flatMap((a) => a.tables)].map((t) => <Table key={t.id} t={t} und={und} />)}
          {working && <Working c={c} say={say} />}
        </div>}
      </div>
    </article>
  );
}
