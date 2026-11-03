import { GoalAnswer, ReportView } from "../api";
import { humanise, Understanding } from "../display";
import { useState } from "react";
import { ReportCard as Card } from "../api";
import { themeOf, THEMES, Theme } from "../insightwidget";
import InsightWidget from "./InsightWidget";
import { Sections, ShowAllLink, ShowMore, TabViewBar, ViewItem, ViewTitle } from "./view/ViewUI";
import { useView } from "./view/ViewContext";

/** The Insights tab in three tiers: the answer, the evidence (full cards, each with its chart and sentence open), and the
 *  lower-ranked findings with the data profile in one clearly named section at the end.
 *  Short view (only when asked for): each insight is one line plus its chart, the top 5 first, the rest under "Show all". */
const MORE_ROWS = 4;           // lower-ranked findings visible before "Show all (n)"

export default function Tiers({ view, goalAnswer, understanding }: { view: ReportView; goalAnswer?: GoalAnswer | null; understanding?: Understanding | null }) {
  const v = useView();
  const short = !!v?.isShort("insights");
  const say = (t: string) => humanise(t, understanding);
  const p = view.profile;
  const profileLines = p.distributions.length + p.outliers.length + p.composition.length;
  const [moreAll, setMoreAll] = useState(false);
  // an action chip on a finding when one of the report's actions rests on it
  const actionFor = (c: Card): string | undefined => {
    const a = view.actions.find((x) => x.evidence.some((e) => c.members.includes(e) || e === c.id));
    if (!a) return undefined;
    const t = a.text; const i = t.indexOf(":");
    return say(i > 0 ? t.slice(0, i) : t);
  };
  // findings grouped by theme; themes ordered by their most important finding, findings keep the report's own order inside a theme
  const themed = (cards: Card[]): [Theme, { c: Card; i: number }[]][] => {
    const by = new Map<Theme, { c: Card; i: number }[]>();
    cards.forEach((c, i) => { const t = themeOf(c.tool); by.set(t, [...(by.get(t) ?? []), { c, i }]); });
    return [...by.entries()];
  };
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
        {heading("insights:evidence", "What we found", <> <span className="muted small">{view.evidence.length} findings, most important first</span></>)}
        {themed(view.evidence).map(([theme, cards]) => (
          <section className="iw-theme" key={theme} aria-label={THEMES[theme].title}>
            <h4 className="iw-theme-title">{THEMES[theme].title}<small>{THEMES[theme].blurb}</small></h4>
            <div className="iw-grid">
              <ShowMore tab="insights" items={cards.map(({ c, i }) => ({ id: `insights:card:${c.id}`, node: (
                <ViewItem key={c.id} id={`insights:card:${c.id}`} tag="div" className="view-card"><InsightWidget c={c} index={i + 1} compact={short} understanding={understanding} action={actionFor(c)} /></ViewItem>) }))} />
            </div>
          </section>
        ))}
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
      <section className="iw-theme" aria-label="More findings">
        {view.appendix.length > 0 && (
          <>
            {heading("insights:appendix", "More findings", <> <span className="muted small">{view.appendix.length} that ranked lower</span></>)}
            <div className="iw-grid">
              {(moreAll ? view.appendix : view.appendix.slice(0, MORE_ROWS)).map((c, i) => (
                <ViewItem key={c.id} id={`insights:card:${c.id}`} className="view-card"><InsightWidget c={c} index={view.evidence.length + i + 1} compact={short} understanding={understanding} action={actionFor(c)} /></ViewItem>
              ))}
            </div>
            {view.appendix.length > MORE_ROWS && (
              <p className="no-print"><button type="button" className="chip" aria-expanded={moreAll} onClick={() => setMoreAll(!moreAll)}>{moreAll ? `Show ${MORE_ROWS}` : `Show all (${view.appendix.length})`}</button></p>
            )}
          </>
        )}
        {profileLines > 0 && (
          <details className="fold fold-working">
            <summary>How we got this<span className="fold-hint">how your columns look ({profileLines} {profileLines === 1 ? "line" : "lines"})</span></summary>
            {([["Distributions", p.distributions], ["Outliers", p.outliers], ["Composition", p.composition]] as const).map(([title, items]) =>
              items.length > 0 && (
                <section key={title}>
                  <h5>{title}</h5>
                  <ul className="tight">{items.map((x) => <li key={x.id} title={`Reference ${x.id}`}>{say(x.line.replace(/\*\*/g, ""))}</li>)}</ul>
                </section>
              ))}
          </details>
        )}
      </section>
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
