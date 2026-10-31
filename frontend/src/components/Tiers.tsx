import { GoalAnswer, ReportView } from "../api";
import ReportCard from "./ReportCard";

/** The Insights tab in three tiers: the answer, the evidence (full cards), and an appendix that stays folded until opened. */
export default function Tiers({ view, goalAnswer }: { view: ReportView; goalAnswer?: GoalAnswer | null }) {
  const p = view.profile;
  const profileLines = p.distributions.length + p.outliers.length + p.composition.length;
  return (
    <div className="tiers">
      <section className="tier1">
        <h3>The answer</h3>
        {goalAnswer && <p className="lead"><strong>{goalAnswer.sentence}</strong> {goalAnswer.groups_sentence}</p>}
        {view.answers.length > 0 && (
          <ul className="tight">
            {view.answers.map((a) => <li key={a.question}><strong>{a.question}</strong> {a.answer}</li>)}
          </ul>
        )}
        {view.headlines.length > 0 && (
          <>
            <h5>Headline findings</h5>
            <ul className="tight">{view.headlines.map((h) => <li key={h.id} title={`Reference ${h.id}`}>{h.text}</li>)}</ul>
          </>
        )}
        {view.actions.length > 0 && (
          <>
            <h5>What to do</h5>
            <ul className="tight">{view.actions.map((a, i) => <li key={i} title={a.evidence.length ? `Based on ${a.evidence.join(", ")}` : undefined}>{a.text}</li>)}</ul>
          </>
        )}
      </section>

      <h3>Evidence <span className="muted small">{view.evidence.length} findings</span></h3>
      {view.evidence.map((c, i) => <ReportCard key={c.id} c={c} index={i + 1} />)}
      {!view.evidence.length && <p>No finding was named by the goal or reliable with a medium or larger effect.</p>}

      {view.checked.length > 0 && (
        <section>
          <h3>Checked, nothing found</h3>
          <ul className="tight">{view.checked.map((c) => <li key={c.id} title={`Reference ${c.id}`}>{c.line}{c.check ? ` ${c.check}` : ""}</li>)}</ul>
        </section>
      )}

      <details className="fold appendix">
        <summary>Appendix: {view.appendix.length} more findings, and the data profile</summary>
        {view.appendix.map((c, i) => <ReportCard key={c.id} c={c} index={i + 1} />)}
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
    </div>
  );
}
