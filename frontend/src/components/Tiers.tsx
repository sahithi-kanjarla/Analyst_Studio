import { GoalAnswer, ReportView } from "../api";
import ReportCard from "./ReportCard";
import { Sections, ShowMore, TabViewBar, ViewItem, ViewTitle } from "./view/ViewUI";
import { useView } from "./view/ViewContext";

/** The Insights tab in three tiers: the answer, the evidence (full cards), and an appendix that stays folded until opened.
 *  Short view: each insight is one line plus its chart, the top 5 by the report's own order are shown, the rest sit under "Show more". */
export default function Tiers({ view, goalAnswer }: { view: ReportView; goalAnswer?: GoalAnswer | null }) {
  const v = useView();
  const short = !!v?.isShort("insights");
  const p = view.profile;
  const profileLines = p.distributions.length + p.outliers.length + p.composition.length;
  const heading = (id: string, text: string, extra?: React.ReactNode) => <h3><ViewTitle id={id} text={text} />{extra}</h3>;
  const nodes: Record<string, React.ReactNode> = {
    "insights:answer": (
      <section className="tier1">
        {heading("insights:answer", "The answer")}
        {goalAnswer && <p className="lead"><strong>{goalAnswer.sentence}</strong> {goalAnswer.groups_sentence}</p>}
        {view.answers.length > 0 && !short && (
          <ul className="tight">
            {view.answers.map((a) => <li key={a.question}><strong>{a.question}</strong> {a.answer}</li>)}
          </ul>
        )}
        {view.headlines.length > 0 && (
          <>
            <h5>Headline findings</h5>
            <ul className="tight">
              <ShowMore tab="insights" items={view.headlines.map((h) => ({ id: `insights:headline:${h.id}`, node: (
                <ViewItem key={h.id} id={`insights:headline:${h.id}`} tag="li" inline><span title={`Reference ${h.id}`}>{h.text}</span></ViewItem>) }))} />
            </ul>
          </>
        )}
        {view.actions.length > 0 && (
          <>
            <h5>What to do</h5>
            <ul className="tight">
              <ShowMore tab="insights" items={view.actions.map((a, i) => ({ id: `insights:action:${i}`, node: (
                <ViewItem key={i} id={`insights:action:${i}`} tag="li" inline askLens={false}><span title={a.evidence.length ? `Based on ${a.evidence.join(", ")}` : undefined}>{a.text}</span></ViewItem>) }))} />
            </ul>
          </>
        )}
      </section>
    ),
    "insights:evidence": (
      <>
        {heading("insights:evidence", "Evidence", <> <span className="muted small">{view.evidence.length} findings</span></>)}
        <ShowMore tab="insights" items={view.evidence.map((c, i) => ({ id: `insights:card:${c.id}`, node: (
          <ViewItem key={c.id} id={`insights:card:${c.id}`} tag="div" className="view-card"><ReportCard c={c} index={i + 1} compact={short} /></ViewItem>) }))} />
        {!view.evidence.length && <p>No finding was named by the goal or reliable with a medium or larger effect.</p>}
      </>
    ),
    "insights:checked": view.checked.length > 0 ? (
      <section>
        {heading("insights:checked", "Checked, nothing found")}
        <ul className="tight">
          <ShowMore tab="insights" items={view.checked.map((c) => ({ id: `insights:checked:${c.id}`, node: (
            <ViewItem key={c.id} id={`insights:checked:${c.id}`} tag="li" inline askLens={false}><span title={`Reference ${c.id}`}>{c.line}{c.check ? ` ${c.check}` : ""}</span></ViewItem>) }))} />
        </ul>
      </section>
    ) : <></>,
    "insights:appendix": (
      <details className="fold appendix">
        <summary><ViewTitle id="insights:appendix" text={`Appendix: ${view.appendix.length} more findings, and the data profile`} /></summary>
        {view.appendix.map((c, i) => (
          <ViewItem key={c.id} id={`insights:card:${c.id}`} className="view-card"><ReportCard c={c} index={i + 1} compact={short} /></ViewItem>
        ))}
        {profileLines > 0 && (
          <details className="fold">
            <summary>Data profile ({profileLines} lines)</summary>
            {([["Distributions", p.distributions], ["Outliers", p.outliers], ["Composition", p.composition]] as const).map(([title, items]) =>
              items.length > 0 && (
                <section key={title}>
                  <h5>{title}</h5>
                  <ul className="tight">{items.map((x) => <li key={x.id} title={`Reference ${x.id}`}>{x.line.replace(/\*\*/g, "")}</li>)}</ul>
                </section>
              ))}
          </details>
        )}
      </details>
    ),
  };
  return (
    <div className="tiers">
      <TabViewBar tab="insights" />
      <Sections tab="insights" selfTitled={["insights:answer", "insights:evidence", "insights:checked", "insights:appendix"]} nodes={nodes} />
    </div>
  );
}
