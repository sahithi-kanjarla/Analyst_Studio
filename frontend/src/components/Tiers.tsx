import { GoalAnswer, ReportView } from "../api";
import { humanise, Understanding } from "../display";
import ReportCard from "./ReportCard";
import { Sections, ShowAllLink, ShowMore, TabViewBar, ViewItem, ViewTitle } from "./view/ViewUI";
import { useView } from "./view/ViewContext";

/** The Insights tab in three tiers: the answer, the evidence (full cards, each with its chart and sentence open), and the
 *  lower-ranked findings with the data profile in one clearly named section at the end.
 *  Short view (only when asked for): each insight is one line plus its chart, the top 5 first, the rest under "Show all". */
export default function Tiers({ view, goalAnswer, understanding }: { view: ReportView; goalAnswer?: GoalAnswer | null; understanding?: Understanding | null }) {
  const v = useView();
  const short = !!v?.isShort("insights");
  const say = (t: string) => humanise(t, understanding);
  const p = view.profile;
  const profileLines = p.distributions.length + p.outliers.length + p.composition.length;
  const heading = (id: string, text: string, extra?: React.ReactNode) => <h3><ViewTitle id={id} text={text} />{extra}</h3>;
  const nodes: Record<string, React.ReactNode> = {
    "insights:answer": (
      <section className="tier1">
        {heading("insights:answer", "The answer")}
        {goalAnswer && <p className="lead" data-kind="takeaway"><strong>{say(goalAnswer.sentence)}</strong> {say(goalAnswer.groups_sentence)}</p>}
        {view.answers.length > 0 && !short && (
          <ul className="tight">
            {view.answers.map((a) => <li key={a.question}><strong>{say(a.question)}</strong> {say(a.answer)}</li>)}
          </ul>
        )}
        {view.headlines.length > 0 && (
          <>
            <h5>Headline findings</h5>
            <ul className="tight">
              <ShowMore tab="insights" items={view.headlines.map((h) => ({ id: `insights:headline:${h.id}`, node: (
                <ViewItem key={h.id} id={`insights:headline:${h.id}`} tag="li"><span title={`Reference ${h.id}`}>{say(h.text)}</span></ViewItem>) }))} />
            </ul>
          </>
        )}
        {view.actions.length > 0 && (
          <>
            <h5>What to do</h5>
            <ul className="tight">
              <ShowMore tab="insights" items={view.actions.map((a, i) => ({ id: `insights:action:${i}`, node: (
                <ViewItem key={i} id={`insights:action:${i}`} tag="li" data-kind="action"><span title={a.evidence.length ? `Based on ${a.evidence.join(", ")}` : undefined}>{say(a.text)}</span></ViewItem>) }))} />
            </ul>
          </>
        )}
      </section>
    ),
    "insights:evidence": (
      <>
        {heading("insights:evidence", "Evidence", <> <span className="muted small">{view.evidence.length} findings</span></>)}
        <ShowMore tab="insights" items={view.evidence.map((c, i) => ({ id: `insights:card:${c.id}`, node: (
          <ViewItem key={c.id} id={`insights:card:${c.id}`} tag="div" className="view-card"><ReportCard c={c} index={i + 1} compact={short} understanding={understanding} /></ViewItem>) }))} />
        {!view.evidence.length && <p className="empty-state">Nothing in this data was both reliable and big enough to call a finding. What was checked is listed below.</p>}
      </>
    ),
    "insights:checked": view.checked.length > 0 ? (
      <section>
        {heading("insights:checked", "Checked, and nothing stood out")}
        <ul className="tight">
          <ShowMore tab="insights" items={view.checked.map((c) => ({ id: `insights:checked:${c.id}`, node: (
            <ViewItem key={c.id} id={`insights:checked:${c.id}`} tag="li"><span title={`Reference ${c.id}`}>{say(c.line)}{c.check ? ` ${say(c.check)}` : ""}</span></ViewItem>) }))} />
        </ul>
      </section>
    ) : <></>,
    "insights:appendix": (
      <details className="fold appendix fold-working">
        <summary><ViewTitle id="insights:appendix" text={`${view.appendix.length} more ${view.appendix.length === 1 ? "finding" : "findings"} that ranked lower, and how your columns look`} /></summary>
        {view.appendix.map((c, i) => (
          <ViewItem key={c.id} id={`insights:card:${c.id}`} className="view-card"><ReportCard c={c} index={i + 1} compact={short} understanding={understanding} /></ViewItem>
        ))}
        {profileLines > 0 && (
          <details className="fold">
            <summary>How your columns look ({profileLines} {profileLines === 1 ? "line" : "lines"})</summary>
            {([["Distributions", p.distributions], ["Outliers", p.outliers], ["Composition", p.composition]] as const).map(([title, items]) =>
              items.length > 0 && (
                <section key={title}>
                  <h5>{title}</h5>
                  <ul className="tight">{items.map((x) => <li key={x.id} title={`Reference ${x.id}`}>{say(x.line.replace(/\*\*/g, ""))}</li>)}</ul>
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
      <ShowAllLink tab="insights" />
    </div>
  );
}
