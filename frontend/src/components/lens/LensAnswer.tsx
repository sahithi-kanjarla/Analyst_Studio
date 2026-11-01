import { useState } from "react";
import { api, Brief, LensChart, LensHow, LensTable, LensTurn } from "../../api";
import Dialog from "../Dialog";
import { useLens } from "./LensContext";
import { useView } from "../view/ViewContext";

const CHART_LABEL: Record<string, string> = { bar: "Bar", line: "Line", stacked_bar: "Stacked bar", share: "Share", histogram: "Histogram", scatter: "Scatter", table: "Table" };
const GRAIN_LABEL: Record<string, string> = { day: "Daily", week: "Weekly", month: "Monthly", quarter: "Quarterly", year: "Yearly" };
const NUMERIC = /^[\s\d.,%+\-–−₹$€£¥]+$/;

/** A table of the strings the server wrote (one place formats every figure). */
export function LensTableView({ table, max = 6 }: { table: LensTable; max?: number }) {
  const rows = table.shown.slice(0, max);
  return (
    <div className="lens-table-wrap">
      <table className="table lens-table">
        <thead><tr>{table.columns.map((c, i) => <th key={i} className={i > 0 ? "num" : ""}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={j > 0 && NUMERIC.test(c) ? "num" : ""}>{c}</td>)}</tr>)}
        </tbody>
      </table>
      {table.total_rows > rows.length && <p className="muted lens-small">Showing {rows.length} of {table.total_rows.toLocaleString()} rows.</p>}
    </div>
  );
}

/** The lines come from the server (one place writes them), so the box can never be empty. */
export function LensHowView({ how }: { how: LensHow }) {
  const lines = how.lines?.length ? how.lines : [`${how.operation || "Answered from this run's saved records"}.`];
  const [first, ...rest] = lines;
  const foot = rest.length > 1 ? rest[rest.length - 1] : "";
  const body = foot ? rest.slice(0, -1) : rest;
  return (
    <div className="lens-how">
      <p><strong>{first}</strong></p>
      {body.length > 0 && <ul>{body.map((l, i) => <li key={i}>{l}</li>)}</ul>}
      {foot && <p className="muted lens-small">{foot}</p>}
      {how.sql && <details className="lens-sql"><summary>The query that was run</summary><pre>{how.sql}</pre></details>}
    </div>
  );
}

/** One brief, in its fixed layout: headline, key figures, recommendations, charts, then the data-quality note (always shown, with the trust label). */
export function BriefView({ b, runId, busy, onAsk }: { b: Brief; runId: string; busy: boolean; onAsk: (q: string) => void }) {
  const mark = (x: { edited?: boolean }) => (x.edited ? <small className="view-edited"> Edited by you</small> : null);
  const copy = async () => {
    try {
      const r = await fetch(`/api/runs/${runId}/export/text?brief=${b.id}`);
      await navigator.clipboard.writeText(await r.text());
    } catch {
      /* clipboard can be blocked: the text link still works */
    }
  };
  return (
    <article className="lens-brief">
      <h4>{b.title}{b.source !== "ai" && <small className="muted"> · from the report's own sentences</small>}</h4>
      {b.no_info ? <p className="lens-brief-head">{b.no_info}</p> : b.headline && <p className="lens-brief-head">{b.headline.text}{mark(b.headline)}</p>}
      {b.key_figures.length > 0 && <ul className="lens-brief-figs">{b.key_figures.map((x, i) => <li key={i}>{x.text}{mark(x)}</li>)}</ul>}
      {b.recommendations.length > 0 && (<><h5>What to do</h5><ol>{b.recommendations.map((x, i) => <li key={i}>{x.text}{mark(x)}</li>)}</ol></>)}
      {b.charts.map((c) => <figure key={c.path}><img src={`/api/runs/${runId}/files/${c.path}`} alt={c.title} loading="lazy" /><figcaption className="muted lens-small">{c.title}</figcaption></figure>)}
      <div className="lens-brief-quality" role="note">
        <strong>Data quality · How sure we are: {b.quality.trust}</strong>
        {b.quality.notes.length > 0 && <ul>{b.quality.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
      </div>
      <div className="msg-tools lens-tools no-print">
        <a className="link" href={`/api/runs/${runId}/export/brief?brief=${b.id}`}>Download PDF</a>
        <button className="link" onClick={copy}>Copy as text</button>
        <button className="link" disabled={busy} onClick={() => onAsk(`delete the ${b.title} brief`)}>Delete</button>
      </div>
    </article>
  );
}

function csv(t: LensTable) {
  const q = (s: string) => `"${s.replace(/"/g, '""')}"`;
  return "﻿" + [t.columns.map(q).join(","), ...t.shown.map((r) => r.map(q).join(","))].join("\n");
}

function Picture({ chart, onOpen }: { chart: LensChart; text?: string; onOpen?: () => void }) {
  return (
    <button type="button" className="lens-pic" onClick={onOpen} aria-label="Open the chart larger" disabled={!onOpen}>
      <img src={chart.url} alt="" loading="lazy" />
    </button>
  );
}

/** A thumbs up or down on one answer; it is recorded with the plan and the tools used, so the most common failures can be found later. */
function Thumbs({ turnId, runId, initial }: { turnId: string; runId: string; initial?: "up" | "down" }) {
  const [vote, setVote] = useState<"up" | "down" | undefined>(initial);
  const send = (v: "up" | "down") => {
    setVote(v);
    api.lensFeedback(runId, turnId, v).catch(() => setVote(undefined));
  };
  return (
    <div className="lens-thumbs no-print" role="group" aria-label="Was this answer useful?">
      <button className={`chip ${vote === "up" ? "chip-on" : ""}`} aria-pressed={vote === "up"} aria-label="This answer helped" onClick={() => send("up")}>👍</button>
      <button className={`chip ${vote === "down" ? "chip-on" : ""}`} aria-pressed={vote === "down"} aria-label="This answer did not help" onClick={() => send("down")}>👎</button>
    </div>
  );
}

/** One answer: the sentence, the chart or table, how it was calculated, the tools, the chart controls and what to ask next. */
export default function LensAnswerView({ turn, last }: { turn: LensTurn; last: boolean }) {
  const lens = useLens();
  const view = useView();
  const a = turn.answer;
  const [big, setBig] = useState(false);
  if (!lens) return null;
  const hasPic = !!a.chart?.path;
  const fix = (set: Record<string, unknown>, label: string) => lens.send({ patch: { turn: turn.id, set } }, label);
  const pinBtn = (target: "report" | "dashboard", label: string) => {
    const p = lens.pinOf(turn.id, target);
    return <button className="link" aria-pressed={!!p} onClick={() => (p ? lens.unpin(p.id) : lens.pin(turn.id, target))}>{p ? `Unpin from ${target}` : label}</button>;
  };
  return (
    <div className={`lens-msg lens-bot lens-${a.status}`}>
      {a.label && <p className="lens-badge">{a.label}</p>}
      {a.focus && <p className="muted lens-small lens-about">About: {a.focus.title}</p>}
      <p className="lens-text">{a.status === "clarify" ? a.clarify?.question : a.text}</p>
      {a.briefs && a.briefs.length > 0 && <div className="lens-briefs">{a.briefs.map((b) => <BriefView key={b.id} b={b} runId={lens.runId} busy={lens.busy} onAsk={(q) => lens.send({ question: q }, q)} />)}</div>}
      {a.briefs && a.briefs.length > 1 && <div className="msg-tools lens-tools no-print"><a className="link" href={`/api/runs/${lens.runId}/export/all_briefs`}>Download all briefs (zip of PDFs)</a></div>}
      {a.export && (
        <div className="msg-tools lens-tools no-print">
          {a.export.what !== "text" && a.export.browser && <a className="btn-primary lens-export" href={a.export.url}>{a.export.label}</a>}
          {a.export.text !== undefined && <button className="btn-primary lens-export" onClick={() => void navigator.clipboard?.writeText(a.export!.text ?? "")}>Copy as text</button>}
          {a.export.text !== undefined && <pre className="lens-export-text">{a.export.text}</pre>}
        </div>
      )}
      {a.second_method && (
        <div className="lens-second"><p><strong>{a.second_method.label}</strong>: {a.second_method.text}</p><LensTableView table={a.second_method.table} /></div>
      )}
      {a.kind === "view" && a.view?.undo && last && (
        <div className="msg-tools lens-tools no-print">
          <button className="chip" disabled={lens.busy || !view?.data.can_undo} onClick={() => view?.undo()}>Undo</button>
          <span className="muted lens-small">One click puts the view back. Nothing in your report was changed or deleted.</span>
        </div>
      )}
      {a.status === "answered" && !a.table && (
        <>
          {a.lines && a.lines.length > 0 && <ul className="lens-lines">{a.lines.map((l, i) => <li key={i}>{l}</li>)}</ul>}
          {a.actions && a.actions.length > 0 && (
            <div className="lens-actions"><span className="lens-label">Top actions</span><ol>{a.actions.map((l, i) => <li key={i}>{l}</li>)}</ol></div>
          )}
          {a.how && <details className="lens-details"><summary>How this was calculated</summary><LensHowView how={a.how} /></details>}
          {a.kind === "summary" && a.view_offer && (
            <div className="msg-tools lens-tools no-print">
              {lens.shortView?.from_turn === turn.id
                ? <span className="muted lens-small">Kept as your short version. The original report is unchanged.</span>
                : <button className="link" disabled={lens.busy} onClick={() => lens.saveShort(turn.id)}>{a.view_offer.label}</button>}
            </div>
          )}
        </>
      )}
      {a.status === "clarify" && a.clarify && (
        <div className="lens-chips" role="group" aria-label="Choose one">
          {a.clarify.choices.map((c) => <button key={c.id} className="chip" disabled={!last || lens.busy} onClick={() => lens.send({ choice: { turn: turn.id, choice: c.id } }, c.label)}>{c.label}</button>)}
        </div>
      )}
      {a.status === "answered" && a.table && (
        <>
          {hasPic ? <Picture chart={a.chart!} text={a.text} onOpen={() => setBig(true)} /> : <LensTableView table={a.table} />}
          {a.chart?.note && <p className="muted lens-small">{a.chart.note}</p>}
          {a.how && <details className="lens-details"><summary>How this was calculated</summary><LensHowView how={a.how} /></details>}
          {a.controls && (
            <details className="lens-details">
              <summary>Change the chart</summary>
              <div className="lens-controls">
                <div role="group" aria-label="Chart type"><span className="lens-label">Show as</span>
                  {a.controls.chart_types.map((c) => <button key={c} className={`chip ${a.controls!.chart === c ? "chip-on" : ""}`} aria-pressed={a.controls!.chart === c} disabled={lens.busy || a.controls!.chart === c} onClick={() => fix({ chart: c }, `Show as ${CHART_LABEL[c]?.toLowerCase() ?? c}`)}>{CHART_LABEL[c] ?? c}</button>)}
                </div>
                {a.controls.grains.length > 0 && (
                  <div role="group" aria-label="Time grain"><span className="lens-label">Group by</span>
                    {a.controls.grains.map((g) => <button key={g} className={`chip ${a.query?.grain === g ? "chip-on" : ""}`} aria-pressed={a.query?.grain === g} disabled={lens.busy || a.query?.grain === g} onClick={() => fix({ grain: g }, `${GRAIN_LABEL[g]} view`)}>{GRAIN_LABEL[g]}</button>)}
                  </div>
                )}
                {a.controls.can_top_n && (
                  <div role="group" aria-label="How many to show"><span className="lens-label">Show</span>
                    {[3, 5, 10, 20].map((n) => <button key={n} className={`chip ${a.query?.n === n ? "chip-on" : ""}`} aria-pressed={a.query?.n === n} disabled={lens.busy || a.query?.n === n} onClick={() => fix({ n }, `Show ${n}`)}>{n}</button>)}
                  </div>
                )}
              </div>
            </details>
          )}
          <div className="msg-tools lens-tools no-print">
            <button className="link" onClick={() => setBig(true)}>Expand</button>
            {pinBtn("report", "Pin to report")}
            {pinBtn("dashboard", "Pin to dashboard")}
            {hasPic
              ? <a className="link" href={a.chart!.url} download={`lens-${turn.id}.png`}>Download image</a>
              : <a className="link" href={`data:text/csv;charset=utf-8,${encodeURIComponent(csv(a.table))}`} download={`lens-${turn.id}.csv`}>Download table</a>}
          </div>
          {big && (
            <Dialog title={turn.question} intro={a.text} onClose={() => setBig(false)} wide>
              {hasPic && <img className="lens-big" src={a.chart!.url} alt="" />}
              <LensTableView table={a.table} max={200} />
              {a.how && <LensHowView how={a.how} />}
            </Dialog>
          )}
        </>
      )}
      {(a.status === "answered" || a.status === "no_fit") && <Thumbs turnId={turn.id} runId={lens.runId} initial={a.feedback} />}
      {last && a.chips.length > 0 && a.status !== "clarify" && (
        <div className="lens-next">
          <span className="lens-label">{a.status === "answered" ? "What to ask next" : "You could ask"}</span>
          <div className="lens-chips">{a.chips.map((c) => <button key={c.id} className="chip" disabled={lens.busy} onClick={() => lens.send({ chip: c.id }, c.text)}>{c.text}</button>)}</div>
        </div>
      )}
    </div>
  );
}
