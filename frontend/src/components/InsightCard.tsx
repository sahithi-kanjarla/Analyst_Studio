import ReactMarkdown from "react-markdown";
import { Finding } from "../api";
import { plainTitle } from "../display";
import { takeaway } from "../insightwidget";
import ChartFrame from "./ChartFrame";
import HowSteps, { Step, TechDetail } from "./HowSteps";
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
  const sees = takeaway(ins.why_it_matters ?? "", ins.observation, 230);
  const steps: Step[] = [
    { icon: "scale", text: <>How far to trust it: <strong>{ins.confidence.label.toLowerCase()}</strong>.</> },
    ...(st.n != null ? [{ icon: "rows", text: <>Checked on {Number(st.n).toLocaleString()} rows.</> }] : []),
    ...ins.confidence.reasons.slice(0, 2).map((r) => ({ icon: "check", text: r })),
  ];
  return (
    <article className="insight ic-short" data-index={index}>
      <header className="insight-head">
        <div className="insight-title">
          <h4>{plainTitle(f.title)}</h4>
          <p className="iw-line">{ins.headline}</p>
          <div className="meta">
            <span className={LEVEL_CLASS[ins.confidence.level] ?? "tag"}>{ins.confidence.label} confidence</span>
            {ins.kpi_related && <span className="tag tag-kpi">your KPI</span>}
            {ins.source === "llm" && <span className="tag tag-info" title="Wording improved by the language model using your business context">AI-tailored</span>}
          </div>
        </div>
      </header>

      <div className="insight-body">
        {f.chart_url ? <ChartFrame src={f.chart_url} alt={f.title} title={plainTitle(f.title)} /> : <FindingTable f={f} />}
        <div className="insight-text">
          {sees && <p className="rp-why">{sees}</p>}
          <details className="fold fold-working">
            <summary>Details, trust and next steps<span className="fold-hint">how far to trust it, in a few lines</span></summary>
            <HowSteps steps={steps} />
            <TechDetail>
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
              {ins.confidence.reasons.length > 2 && (
                <ul className="tight">{ins.confidence.reasons.slice(2).map((r, i) => <li key={i}>{r}</li>)}</ul>
              )}
              {ins.hypotheses.length > 0 && (
                <section>
                  <h5>Ideas to test</h5>
                  <ul className="tight">{ins.hypotheses.map((h, i) => <li key={i}>{h}</li>)}</ul>
                </section>
              )}
              {ins.check_next.length > 0 && (
                <section>
                  <h5>Check next</h5>
                  <ul className="tight">{ins.check_next.map((c, i) => <li key={i}>{c}</li>)}</ul>
                </section>
              )}
              <p className="stats">
                {st.n != null && <>n = {String(st.n)} </>}
                {st.p != null && <>· {pText(st.p)} </>}
                {st.p_adjusted != null && <>· adjusted {pText(st.p_adjusted)}</>}
              </p>
            </TechDetail>
          </details>
        </div>
      </div>
    </article>
  );
}
