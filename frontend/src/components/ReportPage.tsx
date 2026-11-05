import { useState } from "react";
import { ReportCard, RunResult } from "../api";
import { humanise, plainLabel, plainTitle, Understanding } from "../display";
import { Columns } from "./charts";
import Dialog from "./Dialog";
import LensChat from "./lens/LensChat";
import { LensProvider } from "./lens/LensContext";
import LensPins from "./lens/LensPins";
import Md from "./Md";
import PrintPanel from "./PrintPanel";
import ExportMenu, { ExportItem } from "./nav/ExportMenu";
import Menu from "./nav/Menu";
import { Sections, ShowAllLink, ShowMore, ViewImg, ViewItem, ViewTitle } from "./view/ViewUI";
import { useView } from "./view/ViewContext";
import { ReportBody } from "./Results";

/** The action's bold lead, one plain line and the rest, as split by code on the server; the old split is the fallback for a run saved before it. */
function splitAction(a: { text: string; split_lead?: string; split_line?: string; split_rest?: string }): { lead: string; line: string; rest: string } {
  if (a.split_lead !== undefined) return { lead: a.split_lead, line: a.split_line ?? "", rest: a.split_rest ?? "" };
  const i = a.text.indexOf(":");
  const j = a.text.lastIndexOf(":");
  return i === -1 ? { lead: a.text, line: "", rest: "" } : { lead: a.text.slice(0, i).trim(), line: a.text.slice(j + 1).trim(), rest: "" };
}

/** Display only: the statistic in brackets is left out of the page, the sentence itself is unchanged. */
const plainStats = (t: string) => t.replace(/\s*\((?:p\s*[=<>]\s*[\d.]+)[^)]*\)/g, "");
const meaning = (caption: string) => caption.replace(/^How to read this:\s*/i, "");

/** A chart's own title, in plain words and sentence case: no internal column name ever reaches the heading. */
const chartHeading = (title: string, und?: Understanding | null) => {
  const t = humanise(String(title ?? ""), und).replace(/^\s*(total|sum of)\s+/i, "").replace(/\s*\((?:sum|avg|average|mean|count)\)\s*/gi, " ").replace(/\b(Total|Sum of)\s+/g, "").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
};

/** Simple bars with the value at the end of each: no error bars, no n= labels (those stay in the full-size picture and the details). */
function SimpleBars({ items, total }: { items: { label: string; value: number; text: string }[]; total: number }) {
  const max = Math.max(...items.map((i) => Math.abs(i.value)), 0.0001);
  return (
    <div className="rp-bars">
      <ol className="iw-rank">
        {items.map((i) => (
          <li key={i.label}>
            <span className="iw-rank-label" title={i.label}>{i.label}</span>
            <span className="iw-rank-bar" aria-hidden><s style={{ width: `${Math.max(2, (Math.abs(i.value) / max) * 100)}%` }} /></span>
            <b className="iw-rank-val">{i.text}</b>
          </li>
        ))}
      </ol>
      {total > items.length && <p className="rp-bars-note">Top {items.length} of {total.toLocaleString()}</p>}
    </div>
  );
}

/** What a Report card draws: monthly columns for a trend (the Dashboard's per-month chart), simple bars for a comparison, otherwise the finding's own picture. */
function Visual({ c, alt }: { c: ReportCard; alt: string }) {
  const [open, setOpen] = useState(false);
  const L = c.lead;
  const fmt = (v: number) => {
    const a = Math.abs(v);
    return a >= 1e6 ? `${Math.round(v / 1e5) / 10}M` : a >= 1e4 ? `${Math.round(v / 100) / 10}K` : a >= 1000 ? Math.round(v).toLocaleString() : String(Math.round(v * 100) / 100);
  };
  const drawn = L && (L.series.length > 1 || L.bars.length > 1);
  if (!drawn) return c.chart_url ? <div className="rp-viz"><ViewImg id={`report:chart:${c.id}`} src={c.chart_url} alt={alt} /></div> : <div className="rp-viz" />;
  return (
    <figure className="rp-viz rp-viz-drawn chart-frame" data-kind="chart">
      {L.series.length > 1
        ? <Columns items={L.series.map((p) => ({ key: p.key, label: p.label, value: p.value }))} selected={[]} onToggle={() => undefined} fmt={fmt} height={170} />
        : <SimpleBars items={L.bars} total={L.bars_total} />}
      {c.chart_url && <button type="button" className="link chart-frame-open no-print" onClick={() => setOpen(true)}>Open full size</button>}
      {open && c.chart_url && <Dialog title={alt} onClose={() => setOpen(false)} wide><img className="chart-full" src={c.chart_url} alt={alt} /></Dialog>}
    </figure>
  );
}

/** One curated finding in "What matters": a lead figure a reader cares about, a plain title, one plain line, a simple chart, and the working behind it folded. */
function ReportFinding({ c, und }: { c: ReportCard; und?: Understanding | null }) {
  const say = (t: string) => humanise(t, und);
  const cid = `report:finding:${c.id}`;
  const L = c.lead;
  const dir = L && L.sign ? (L.sign > 0 ? "up" : "down") : null;
  return (
    <ViewItem id={cid} tag="article" className="rp-chart rp-matter" data-kind="chart">
      {L?.value ? (
        <p className={`rp-lead ${dir ? `rp-${dir}` : ""}`}><b>{dir === "up" ? "▲ " : dir === "down" ? "▼ " : ""}{L.value}</b><span>{say(L.label)}</span></p>
      ) : (
        <p className="rp-lead"><span className={`rp-trust rp-trust-${c.confidence.level}`}>{c.confidence.label} confidence</span></p>
      )}
      <h3><ViewTitle id={cid} text={plainTitle(c.title, und)} /></h3>
      {L?.line && <p className="iw-line rp-line" data-kind="takeaway">{say(L.line)}</p>}
      <Visual c={c} alt={say(c.title)} />
      <details className="rp-support rp-more-details">
        <summary>Details</summary>
        {L?.why && <p className="rp-why" data-kind="reason">{say(L.why)}</p>}
        {L?.ratio && <p>{L.ratio}</p>}
        {L?.difference && <p>This is {L.difference}.</p>}
        <p>How far to trust it: {c.confidence.label.toLowerCase()}.{c.stats.n != null ? ` Checked on ${Number(c.stats.n).toLocaleString()} records.` : ""}</p>
        {L && L.merged.length > 0 && <ul className="muted small">{L.merged.map((t, i) => <li key={i}>{say(t)}</li>)}</ul>}
      </details>
    </ViewItem>
  );
}

/** The confidence chip: a short plain label; the explanation opens on hover (title) and on tap or Enter. */
function ConfidenceChip({ trust, why }: { trust: string; why: string }) {
  const [open, setOpen] = useState(false);
  const word = trust.toLowerCase();
  const text = `Confidence in the answer and the key figures on this page.${why ? ` Why: ${why}.` : ""}`;
  return (
    <span className="rp-conf">
      <button type="button" className="rp-pill rp-conf-chip" title={text} aria-expanded={open} aria-label={`Confidence: ${word}. ${text}`} onClick={() => setOpen((o) => !o)}>Confidence: {word}</button>
      {open && <span className="rp-conf-pop" role="note">{text}</span>}
    </span>
  );
}

/** Edit view: the short or full view, Undo, Reset and the hidden items (Restore each), all in one menu. */
function EditViewMenu() {
  const v = useView();
  if (!v || !v.data.resolved.tabs["report"]) return null;
  const hidden = v.data.state.hidden.filter((id) => v.data.items[id]);
  const short = v.isShort("report");
  return (
    <Menu label="Edit view" className="edit-view-menu">
      {(close) => (
        <>
          <button role="menuitem" type="button" className="menu-item" disabled={v.busy} onClick={() => { close(); void v.run({ cmd: "set_mode", tab: "report", mode: short ? "full" : "short" }); }}>{short ? "Show the full view" : "Show the short view"}</button>
          {v.data.can_undo && <button role="menuitem" type="button" className="menu-item" disabled={v.busy} onClick={() => { close(); void v.undo(); }}>Undo last change</button>}
          <button role="menuitem" type="button" className="menu-item" disabled={v.busy || !v.data.bar?.show} onClick={() => { close(); void v.reset(); }}>Reset the view</button>
          {hidden.map((id) => <button key={id} role="menuitem" type="button" className="menu-item" onClick={() => { close(); void v.show(id); }}>Restore: {v.title(id, v.data.items[id].title)}</button>)}
        </>
      )}
    </Menu>
  );
}

/** The report as a designed page: the short answer, the key figures, what to do, the evidence, and everything else folded away. */
export default function ReportPage({ run, embedded = false, startChat = false, startPrint = false, onExport }: { run: RunResult; embedded?: boolean; startChat?: boolean; startPrint?: boolean; onExport?: (item: ExportItem) => void }) {
  const page = run.report_view?.page;
  const view = useView();
  const short = !!view?.isShort("report");
  if (!page) {
    return (
      <>
        {embedded && <div className="rp-actions no-print"><PrintPanel run={run} /></div>}
        <ReportBody md={run.report_md ?? ""} />
        <LensPins target="report" />
      </>
    );
  }
  const title = page.built_title || (page.company ? `${page.company} report` : "Report");
  const tag = page.built_tag ?? (run.report_view?.overview_note ? "Overview, no goal given" : "");
  const note = view?.data.bar?.show ? view.data.bar.text : "";
  const tiles = page.tiles.map((t, i) => ({ ...t, i })).filter((t) => t.label !== "Columns").map((t) => (t.label === "Rows" ? { ...t, label: "Records in your file" } : t));

  const reportFindings = run.report_view?.report_findings ?? [];
  const hasFindings = reportFindings.length > 0;
  
  return (
    <div className={`report-layout ${startChat ? "with-chat" : ""} ${embedded ? "embedded" : ""}`}>
      <article className="report-page" aria-label="Report">
        <header className="rp-head">
          <div className="rp-head-text">
            <h1 className="rp-title">{title}</h1>
            {page.scope && <p className="rp-scope" data-kind="scope">{page.scope.replace(/^Based on\s+/, "").replace(/,\s*from\s+/, ", ").replace(/,\s/g, " · ")}</p>}
          </div>
          <div className="rp-head-side">
            <p className="rp-meta">
              <span>Generated {page.date}</span>
              {page.trust && <ConfidenceChip trust={page.trust} why={page.cards[0]?.trust_why ?? ""} />}
            </p>
            <div className="rp-toolbar no-print">
              {view && <button type="button" className="btn-quiet details-toggle" aria-pressed={view.details} onClick={() => void view.setDetails(!view.details)}>{view.details ? "Collapse details" : "Expand all details"}</button>}
              <EditViewMenu />
              {onExport ? <ExportMenu run={run} onPick={onExport} /> : embedded && <PrintPanel run={run} startOpen={startPrint} />}
            </div>
          </div>
        </header>

        {note && <p className="view-note no-print" role="status">{note}</p>}
        <Sections tab="report" selfTitled={["report:actions", "report:evidence"]} nodes={{
          "report:short": (
          <section className="rp-headline" data-kind="summary" aria-label="The short answer">
          <p className="rp-big rp-answer">{page.built_answer || humanise(plainStats(page.headline), run.understanding)}</p>
          {tag && <span className="rp-tag" data-kind="overview-note">{tag}</span>}
          {page.headline_more && <p className="rp-more">{humanise(page.headline_more, run.understanding)}</p>}
        </section>

          ),
          "report:numbers": (
            tiles.length > 0 ? (
              <section className="rp-tiles grid" data-kind="numbers" aria-label="Key figures">
                <ShowMore tab="report" items={tiles.map((t) => ({ id: `report:tile:${t.i}`, node: (
                  <ViewItem key={t.label} id={`report:tile:${t.i}`} className={`rp-tile ${tiles.length === 3 ? "span-4" : "span-3"}`}>
                    <span className={`rp-tile-value ${t.value.length > 11 ? "long" : ""}`}>{t.value}</span>
                    <span className="rp-tile-label"><ViewTitle id={`report:tile:${t.i}`} text={plainLabel(t.label)} /></span>
                  </ViewItem>) }))} />
              </section>
            ) : <></>
          ),
          "report:actions": (
            <section className="rp-section" data-kind="summary" aria-labelledby="rp-todo">
              <ViewTitle id="report:actions" tag="h2" text="What to do" />
              {page.actions.length > 0 ? (
                <ol className="rp-todo">
                  <ShowMore tab="report" items={page.actions.map((a, i) => {
                    const split = splitAction(a);
                    const rest = [split.rest, a.figure ? a.figure.replace(/^Supported by:\s*/i, "") : ""].filter(Boolean).join(" ");
                    return { id: `report:action:${i}`, node: (
                      <ViewItem key={i} id={`report:action:${i}`} tag="li" className="rp-action" data-kind="action">
                        <span className="rp-num" aria-hidden>{i + 1}</span>
                        <div>
                          <h3><ViewTitle id={`report:action:${i}`} text={humanise(split.lead, run.understanding)} /></h3>
                          {split.line && !short && <p className="rp-action-line">{humanise(split.line, run.understanding)}</p>}
                          {rest && !short && <details className="rp-support"><summary>Based on</summary><p data-kind="evidence">{humanise(rest, run.understanding)}</p></details>}
                        </div>
                      </ViewItem>) };
                  })} />
                </ol>
              ) : (
                <p className="muted">{plainLabel(page.no_action[0] ?? "The data does not support a specific change yet.")}</p>
              )}
            </section>
          ),
          "report:evidence": (
            hasFindings ? (
              <section className="rp-section" data-kind="charts" aria-label="What matters">
                <ViewTitle id="report:evidence" tag="h2" text="What matters" />
                <div className="rp-matters">
                  {reportFindings.map((c) => <ReportFinding key={c.id} c={c} und={run.understanding} />)}
                </div>
              </section>
            ) : page.charts.length > 0 ? (
              <section className="rp-section" data-kind="charts" aria-label="What matters">
                <ViewTitle id="report:evidence" tag="h2" text="What matters" />
                <div className="rp-matters">
                  <ShowMore tab="report" items={page.charts.slice(0, 6).map((c) => {
                    const cid = `report:chart:${c.path.split("/").pop()!.replace(/\.\w+$/, "")}`;
                    return { id: cid, node: (
                      <ViewItem key={c.path} id={cid} tag="article" className="rp-chart rp-matter" data-kind="chart">
                        <h3><ViewTitle id={cid} text={chartHeading(c.title, run.understanding)} /></h3>
                        <ViewImg id={cid} src={c.url ?? ""} alt={c.title} />
                        <figcaption className="iw-line rp-cap" data-kind="takeaway">{humanise(meaning(c.caption), run.understanding)}</figcaption>
                      </ViewItem>) };
                  })} />
                </div>
              </section>
            ) : <></>
          ),
        }} />
        <ShowAllLink tab="report" />

        {(page.more ?? []).length > 0 && (
          <section className="rp-section rp-more-sections" data-kind="details" aria-label="More detail">
            {(page.more ?? []).map((m, i) => (
              <details key={i} className="fold" data-kind="details">
                <summary>{m.title}</summary>
                <Md md={m.md} thumbs />
              </details>
            ))}
          </section>
        )}

        <LensPins target="report" />

        {page.footnotes && page.footnotes.length > 0 && (
          <footer className="rp-foot rp-notes" data-kind="footnotes" aria-label="Notes on the figures">
            {page.footnotes.map((n, i) => <p key={i}>{n}</p>)}
          </footer>
        )}
      </article>
      {startChat && (
        <aside className="lens-embedded no-print" aria-label="Lens, ask your data">
          <LensProvider runId={run.run_id}><LensChat variant="embedded" /></LensProvider>
        </aside>
      )}
    </div>
  );
}
