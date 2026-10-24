import ReactMarkdown from "react-markdown";
import { CardTable, ReportCard as Card } from "../api";

const LEVEL_CLASS: Record<string, string> = {
  high: "tag tag-ok",
  medium: "tag tag-mid",
  low: "tag tag-warn",
  exploratory: "tag tag-info",
  descriptive: "tag",
};

function Table({ t }: { t: CardTable }) {
  return (
    <details className="fold">
      <summary>Per-group figures: {t.title}</summary>
      <div className="tablewrap">
        <table className="table">
          <thead>
            <tr>{t.columns.map((c) => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {t.rows.map((r, i) => (
              <tr key={i}>{r.map((v, j) => <td key={j} className={j > 0 ? "num" : ""}>{String(v)}</td>)}</tr>
            ))}
          </tbody>
        </table>
        {t.total > t.rows.length && <p className="muted small">Showing {t.rows.length} of {t.total} rows; the Data tab shows all of them.</p>}
      </div>
    </details>
  );
}

/** One finding with its median check on one line, a ratio of the same measure as a sub-section, and per-group tables collapsed. */
export default function ReportCard({ c, index }: { c: Card; index: number }) {
  const st = c.stats;
  return (
    <article className="insight">
      <header className="insight-head">
        <span className="insight-num">{index}</span>
        <div className="insight-title">
          <h4>{c.headline}</h4>
          <div className="meta">
            <span className={LEVEL_CLASS[c.confidence.level] ?? "tag"} title={c.confidence.reasons.join(" ")}>{c.confidence.label} confidence</span>
            {c.kpi_related && <span className="tag tag-kpi">your KPI</span>}
            <span className="muted">{c.title} · {c.members.join(", ")}</span>
          </div>
        </div>
      </header>

      <div className="insight-body">
        {c.chart_url && <img src={c.chart_url} alt={c.title} loading="lazy" />}
        <div className="insight-text">
          <section>
            <h5>What we see</h5>
            <ReactMarkdown>{c.observation}</ReactMarkdown>
          </section>
          {c.why_it_matters && (
            <section>
              <h5>Why it matters</h5>
              <p>{c.why_it_matters}</p>
            </section>
          )}
          <section>
            <h5>How much to trust it</h5>
            <ul className="tight">
              {c.confidence.reasons.map((r, i) => <li key={i}>{r}</li>)}
              {c.checks.map((x, i) => <li key={`c${i}`}>{x}</li>)}
            </ul>
          </section>
          {c.also.map((a) => (
            <section key={a.id}>
              <h5>Same split, {a.target}</h5>
              <p><strong>{a.headline}.</strong></p>
              <ReactMarkdown>{a.observation}</ReactMarkdown>
              {a.checks.length > 0 && <ul className="tight">{a.checks.map((x, i) => <li key={i}>{x}</li>)}</ul>}
            </section>
          ))}
          {[...c.tables, ...c.also.flatMap((a) => a.tables)].map((t) => <Table key={t.id} t={t} />)}
          {c.hypotheses.length > 0 && (
            <section>
              <h5>Possible explanations to test <span className="muted">(not conclusions)</span></h5>
              <ul className="tight">{c.hypotheses.map((h, i) => <li key={i}>{h}</li>)}</ul>
            </section>
          )}
          {c.check_next.length > 0 && (
            <section>
              <h5>Check next</h5>
              <ul className="tight">{c.check_next.map((x, i) => <li key={i}>{x}</li>)}</ul>
            </section>
          )}
          <p className="stats">
            {st.n != null && <>n = {String(st.n)} </>}
            {st.p != null && <>· {st.p < 0.001 ? "p < 0.001" : `p = ${st.p.toPrecision(2)}`} </>}
            {st.p_adjusted != null && <>· adjusted {st.p_adjusted < 0.001 ? "p < 0.001" : `p = ${st.p_adjusted.toPrecision(2)}`}</>}
          </p>
        </div>
      </div>
    </article>
  );
}
