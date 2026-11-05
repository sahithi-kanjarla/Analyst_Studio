import { GoalAnswer, NullLine, ReportView } from "../api";
import { humanise, plainTitle, Understanding } from "../display";
import { useState } from "react";
import { ReportCard as Card } from "../api";
import { groupOf, GROUPS, Group } from "../insightwidget";
import InsightWidget from "./InsightWidget";
import State from "./State";
import { Sections, ShowAllLink, ShowMore, TabViewBar, ViewImg, ViewItem, ViewTitle } from "./view/ViewUI";
import { useView } from "./view/ViewContext";

/** The Insights tab: the answer, the evidence (full cards, each with its chart and sentence open),
 *  a collapsed "More" list of lower-ranked findings (title + one line, chart draws on open),
 *  and the data profile.
 *  Short view (only when asked for): each insight is one line plus its chart, the top 5 first. */

export default function Tiers({ view, goalAnswer, understanding }: { view: ReportView; goalAnswer?: GoalAnswer | null; understanding?: Understanding | null }) {
  const v = useView();
  const short = !!v?.isShort("insights");
  const say = (t: string) => humanise(t, understanding);
  const p = view.profile;
  const profileLines = p.distributions.length + p.outliers.length + p.composition.length;
  const actionFor = (c: Card): string | undefined => {
    const a = view.actions.find((x) => x.evidence.some((e) => c.members.includes(e) || e === c.id));
    if (!a) return undefined;
    const t = a.text; const i = t.indexOf(":");
    return say(i > 0 ? t.slice(0, i) : t);
  };
  const ORDER: Group[] = ["contributors", "time", "compare"];
  const themed = (cards: Card[]): [Group, { c: Card; i: number }[]][] => {
    const by = new Map<Group, { c: Card; i: number }[]>();
    cards.forEach((c, i) => { const t = groupOf(c.tool); by.set(t, [...(by.get(t) ?? []), { c, i }]); });
    return ORDER.filter((g) => by.has(g)).map((g) => [g, by.get(g)!]);
  };
  const heading = (id: string, text: string, extra?: React.ReactNode) => <h3><ViewTitle id={id} text={text} />{extra}</h3>;

  const curated = !!(view.more_findings);
  const moreFindings = view.more_findings ?? view.appendix;
  const nullLines = view.null_lines ?? [];

  const nodes: Record<string, React.ReactNode> = {
    "insights:answer": (
      <section className="is-strip" aria-label="The answer">
        {heading("insights:answer", "The answer")}
        {goalAnswer && <p className="is-answer" data-kind="takeaway">{say(goalAnswer.sentence)} <span>{say(goalAnswer.groups_sentence)}</span></p>}
        {view.headlines.length > 0 && (
          <ol className="is-points">
            <ShowMore tab="insights" items={view.headlines.slice(0, 3).map((h, i) => ({ id: `insights:headline:${h.id}`, node: (
              <ViewItem key={h.id} id={`insights:headline:${h.id}`} tag="li"><span className="is-n" aria-hidden>{i + 1}</span><span title={`Reference ${h.id}`}>{say(h.text)}</span></ViewItem>) }))} />
          </ol>
        )}
        {((view.answers.length > 0 && !short) || view.headlines.length > 3 || view.actions.length > 0) && (
          <details className="fold fold-working is-more">
            <summary>More on the answer<span className="fold-hint">every question, every headline and what to do</span></summary>
            {view.answers.length > 0 && !short && (
              <ul className="tight">{view.answers.map((a) => <li key={a.question}><strong>{say(a.question)}</strong> {say(a.answer)}</li>)}</ul>
            )}
            {view.headlines.length > 3 && (
              <>
                <h5>Other headline findings</h5>
                <ul className="tight">{view.headlines.slice(3).map((h) => <li key={h.id}><span title={`Reference ${h.id}`}>{say(h.text)}</span></li>)}</ul>
              </>
            )}
            {view.actions.length > 0 && (
              <>
                <h5>What to do</h5>
                <ul className="tight">{view.actions.map((a, i) => <li key={i} data-kind="action"><span title={a.evidence.length ? `Based on ${a.evidence.join(", ")}` : undefined}>{say(a.text)}</span></li>)}</ul>
              </>
            )}
          </details>
        )}
      </section>
    ),
    "insights:evidence": (
      <>
        {heading("insights:evidence", "What we found", <> <span className="muted small">{view.evidence.length} findings, most important first</span></>)}
        {themed(view.evidence).map(([theme, cards]) => (
          <section className="iw-theme" key={theme} aria-label={GROUPS[theme].title}>
            <h4 className="iw-theme-title">{GROUPS[theme].title}<small>{GROUPS[theme].blurb}</small></h4>
            <div className="iw-grid">
              <ShowMore tab="insights" items={cards.map(({ c, i }) => ({ id: `insights:card:${c.id}`, node: (
                <ViewItem key={c.id} id={`insights:card:${c.id}`} tag="div" className="view-card"><InsightWidget c={c} index={i + 1} compact={short} understanding={understanding} action={actionFor(c)} /></ViewItem>) }))} />
            </div>
          </section>
        ))}
        {nullLines.length > 0 && (
          <section className="iw-theme iw-null-lines" aria-label="Checked, nothing found">
            <h4 className="iw-theme-title">Checked, nothing found<small>These looked for a difference and found no reliable one.</small></h4>
            <ul className="chip-list">
              {nullLines.map((nl: NullLine) => <li key={nl.id} className="chip-static" title={nl.reason}>{plainTitle(nl.title, understanding)}</li>)}
            </ul>
          </section>
        )}
        {!view.evidence.length && !nullLines.length && <State kind="empty" title="No finding stood out">Nothing in this data was both reliable and big enough to call a finding. What was checked is listed below.</State>}
      </>
    ),
    "insights:checked": view.checked.length > 0 && nullLines.length === 0 ? (
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
        {moreFindings.length > 0 && (
          curated
            ? <MoreFindingsList findings={moreFindings} say={say} understanding={understanding} offset={view.evidence.length} />
            : <LegacyAppendix findings={moreFindings} view={view} short={short} understanding={understanding} actionFor={actionFor} />
        )}
        {profileLines > 0 && (
          <details className="fold fold-working">
            <summary>Column profile<span className="fold-hint">how your columns look ({profileLines} {profileLines === 1 ? "line" : "lines"})</span></summary>
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

/** Curated "More" list: one collapsed block, each item shows title + one line; chart draws only when opened. */
function MoreFindingsList({ findings, say, understanding, offset }: { findings: Card[]; say: (t: string) => string; understanding?: Understanding | null; offset: number }) {
  return (
    <details className="fold fold-more">
      <summary>More ({findings.length} {findings.length === 1 ? "finding" : "findings"} that ranked lower)</summary>
      <div className="iw-more-list">
        {findings.map((c, i) => (
          <MoreFindingItem key={c.id} c={c} index={offset + i + 1} say={say} understanding={understanding} />
        ))}
      </div>
    </details>
  );
}

/** One item in the "More" list: title and one line; chart draws when the details element is opened. */
function MoreFindingItem({ c, index, say, understanding }: { c: Card; index: number; say: (t: string) => string; understanding?: Understanding | null }) {
  const [opened, setOpened] = useState(false);
  const line = say(c.why_it_matters || c.observation || c.headline);
  return (
    <ViewItem id={`insights:card:${c.id}`} tag="article" className="iw-more-item" data-kind="insight">
      <details onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) setOpened(true); }}>
        <summary>
          <span className="insight-num">{index}</span>
          <div>
            <ViewTitle id={`insights:card:${c.id}`} tag="h4" text={plainTitle(c.title, understanding)} />
            <p className="muted small">{line.length > 140 ? line.slice(0, 137).replace(/[\s,;:]+\S*$/, "") + "…" : line}</p>
          </div>
        </summary>
        {opened && (
          <div className="iw-more-body">
            {c.chart_url && <ViewImg id={`insights:chart:${c.id}`} src={c.chart_url} alt={say(c.title)} />}
            <InsightWidget c={c} index={index} compact understanding={understanding} />
          </div>
        )}
      </details>
    </ViewItem>
  );
}

/** Pre-curation fallback: the old appendix rendering (full cards, "Show all" button). */
function LegacyAppendix({ findings, view, short, understanding, actionFor }: { findings: Card[]; view: ReportView; short: boolean; understanding?: Understanding | null; actionFor: (c: Card) => string | undefined }) {
  const [moreAll, setMoreAll] = useState(false);
  const MORE_ROWS = 4;
  return (
    <>
      <h3>More findings <span className="muted small">{findings.length} that ranked lower</span></h3>
      <div className="iw-grid">
        {(moreAll ? findings : findings.slice(0, MORE_ROWS)).map((c, i) => (
          <ViewItem key={c.id} id={`insights:card:${c.id}`} className="view-card"><InsightWidget c={c} index={view.evidence.length + i + 1} compact={short} understanding={understanding} action={actionFor(c)} /></ViewItem>
        ))}
      </div>
      {findings.length > MORE_ROWS && (
        <p className="no-print"><button type="button" className="chip" aria-expanded={moreAll} onClick={() => setMoreAll(!moreAll)}>{moreAll ? `Show ${MORE_ROWS}` : `Show all (${findings.length})`}</button></p>
      )}
    </>
  );
}
