import ReactMarkdown from "react-markdown";
import { Finding } from "../api";
import FindingTable from "./FindingTable";

const LEVEL_CLASS: Record<string, string> = {
  high: "tag tag-ok",
  medium: "tag tag-mid",
  low: "tag tag-warn",
  exploratory: "tag tag-info",
  descriptive: "tag",
};

function pText(p?: number) {
  if (p == null) return "";
  return p < 0.001 ? "p < 0.001" : `p = ${p.toPrecision(2)}`;
}

export default function InsightCard({ f, index }: { f: Finding; index: number }) {
  const ins = f.insight;
  if (!ins) return null;
  const st = f.stats;
  return (
    <article className="insight">
      <header className="insight-head">
        <span className="insight-num">{index}</span>
        <div className="insight-title">
          <h4>{ins.headline}</h4>
          <div className="meta">
            <span className={LEVEL_CLASS[ins.confidence.level] ?? "tag"}>
              {ins.confidence.label} confidence
            </span>
            {ins.kpi_related && <span className="tag tag-kpi">your KPI</span>}
            {ins.source === "llm" && <span className="tag tag-info" title="Wording improved by the language model using your business context">AI-tailored</span>}
            <span className="muted">{f.title}</span>
          </div>
          {ins.confidence.reasons.length > 0 && <p className="muted small insight-reasons">{ins.confidence.reasons.join(" ")}</p>}
        </div>
      </header>

      <div className="insight-body">
        {f.chart_url ? <img src={f.chart_url} alt={f.title} loading="lazy" /> : <FindingTable f={f} />}
        <div className="insight-text">
          <section>
            <h5>What we see</h5>
            <ReactMarkdown>{ins.observation}</ReactMarkdown>
          </section>
          {ins.why_it_matters && (
            <section>
              <h5>Why it matters</h5>
              <p>{ins.why_it_matters}</p>
            </section>
          )}
          <section>
            <h5>How much to trust it</h5>
            <ul className="tight">
              {ins.confidence.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </section>
          {ins.hypotheses.length > 0 && (
            <section>
              <h5>Possible explanations to test <span className="muted">(not conclusions)</span></h5>
              <ul className="tight">
                {ins.hypotheses.map((h, i) => (
                  <li key={i}>{h}</li>
                ))}
              </ul>
            </section>
          )}
          {ins.check_next.length > 0 && (
            <section>
              <h5>Check next</h5>
              <ul className="tight">
                {ins.check_next.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </section>
          )}
          <p className="stats">
            {st.n != null && <>n = {String(st.n)} </>}
            {st.p != null && <>· {pText(st.p)} </>}
            {st.p_adjusted != null && <>· adjusted {pText(st.p_adjusted)}</>}
          </p>
        </div>
      </div>
    </article>
  );
}
