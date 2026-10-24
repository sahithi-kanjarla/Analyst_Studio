import AgentFlow from "./AgentFlow";

const BARS = [
  { l: "South", v: 92 },
  { l: "West", v: 71 },
  { l: "North", v: 58 },
  { l: "East", v: 36 },
  { l: "Central", v: 24 },
];

/** A small, clearly-labelled sample of what a finished analysis looks like. */
export function HeroPreview() {
  return (
    <div className="pv" aria-label="Sample of an analysis result">
      <div className="pv-bar">
        <i /><i /><i />
        <span>sample result</span>
      </div>
      <div className="pv-goal"><small>Goal</small>Which region drives the most revenue, and is it growing?</div>
      <div className="pv-kpis">
        <div><small>Revenue</small><b>₹4.2M</b><em className="up">▲ 12%</em></div>
        <div><small>Orders</small><b>18,430</b><em className="up">▲ 6%</em></div>
        <div><small>Repeat rate</small><b>34%</b><em className="dn">▼ 2%</em></div>
      </div>
      <div className="pv-chart">
        {BARS.map((b, i) => (
          <div className="pv-row" key={b.l}>
            <span>{b.l}</span>
            <div><i style={{ width: `${b.v}%`, animationDelay: `${0.25 + i * 0.08}s` }} /></div>
            <b>{b.v}%</b>
          </div>
        ))}
      </div>
      <div className="pv-find">
        <span className="pv-ok">✓ Critic approved</span>
        <p>South earns <b>3.8×</b> Central and the gap holds after correcting for multiple tests.</p>
        <div className="pv-meta"><span>Confidence <b>High</b></span><span>p &lt; 0.001</span><span>n = 18,430</span></div>
      </div>
    </div>
  );
}

const STEPS = [
  { n: "01", t: "Upload", s: "One or several CSV, Excel or JSON files." },
  { n: "02", t: "Say why", s: "Type your goal in plain English, or ask it to explore." },
  { n: "03", t: "Get answers", s: "A checked report, strategies and a Power BI pack." },
];

export function ThreeSteps() {
  return (
    <ol className="three">
      {STEPS.map((s) => (
        <li key={s.n}>
          <i>{s.n}</i>
          <b>{s.t}</b>
          <span>{s.s}</span>
        </li>
      ))}
    </ol>
  );
}

export function Stats() {
  const items = [
    { v: "11", l: "specialised agents" },
    { v: "8", l: "files linked per run" },
    { v: "100%", l: "of numbers computed in code" },
    { v: "0", l: "paid services required" },
  ];
  return (
    <div className="stats">
      {items.map((s) => (
        <div key={s.l}>
          <b>{s.v}</b>
          <span>{s.l}</span>
        </div>
      ))}
    </div>
  );
}

export function HowItWorks() {
  return (
    <section className="sec" id="how">
      <div className="sec-head">
        <span className="kicker">How it works</span>
        <h2>A team of agents, each with one job</h2>
        <p>Your data moves left to right. Click any agent to see what it does, or let the tour play.</p>
      </div>
      <AgentFlow />
    </section>
  );
}

export function Bento() {
  return (
    <section className="sec">
      <div className="sec-head">
        <span className="kicker">Why you can trust it</span>
        <h2>Built so the answer is checked, not guessed</h2>
      </div>
      <div className="bento">
        <article className="bx bx-wide">
          <h3>Code does the maths. The LLM only writes.</h3>
          <p>Profiling, cleaning, statistics and charts are plain Python. A language model can plan and phrase things, but never supplies a number. Anything it proposes is validated against your real columns.</p>
          <pre className="code" aria-hidden>{`mean(revenue | region=South)  →  4,812.30   # python
"South leads"                  →  wording    # llm (optional)`}</pre>
        </article>

        <article className="bx">
          <h3>Critic review</h3>
          <ul className="checks">
            <li>Multiple-testing correction</li>
            <li>Sample-size and skew checks</li>
            <li>Effect size, not just p-values</li>
            <li>Follow-up re-checks when fragile</li>
          </ul>
        </article>

        <article className="bx">
          <h3>Many files, one model</h3>
          <svg className="mini-er" viewBox="0 0 220 110" role="img" aria-label="Three tables linked on shared keys">
            <path d="M62 36 L110 62 M158 36 L110 62" />
            <g><rect x="10" y="16" width="62" height="32" rx="6" /><text x="41" y="36">orders</text></g>
            <g><rect x="148" y="16" width="62" height="32" rx="6" /><text x="179" y="36">customers</text></g>
            <g className="hub"><rect x="79" y="62" width="62" height="32" rx="6" /><text x="110" y="82">products</text></g>
          </svg>
          <p>Shared keys are found, joins are measured, and the grain follows your goal.</p>
        </article>

        <article className="bx">
          <h3>Asks only when it matters</h3>
          <p>Up to 6 short questions, each backed by a real signal in the data. Nothing ambiguous? It asks nothing. Skipping never changes a number.</p>
          <div className="qpill">0 – 6 questions</div>
        </article>

        <article className="bx">
          <h3>Power BI starter pack</h3>
          <div className="files">
            <span>cleaned_data.csv</span>
            <span>measures.dax</span>
            <span>theme.json</span>
            <span>POWERBI_GUIDE.md</span>
          </div>
          <p>A zip with cleaned data, DAX measures, a theme and a step-by-step guide.</p>
        </article>

        <article className="bx">
          <h3>Free by design</h3>
          <p>Works with a local model, a free-tier API, or no LLM at all using the rule-based planner.</p>
        </article>
      </div>
    </section>
  );
}
