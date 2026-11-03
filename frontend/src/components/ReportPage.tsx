import { RunResult } from "../api";
import { humanise, plainLabel, Understanding } from "../display";
import LensChat from "./lens/LensChat";
import { LensProvider } from "./lens/LensContext";
import LensPins from "./lens/LensPins";
import Md from "./Md";
import PrintPanel from "./PrintPanel";
import { Sections, ShowAllLink, ShowMore, TabViewBar, ViewImg, ViewItem, ViewTitle } from "./view/ViewUI";
import { useView } from "./view/ViewContext";
import { ReportBody } from "./Results";

/** "Put more behind what works: Segment = Enterprise: Grow overall revenue…" -> a short title and one line of why. */
function splitAction(text: string): { title: string; why: string } {
  const i = text.indexOf(":");
  const j = text.lastIndexOf(":");
  if (i === -1) return { title: text, why: "" };
  return { title: text.slice(0, i).trim(), why: text.slice(j + 1).trim() };
}

/** Display only: the statistic in brackets is left to "How we got this", the sentence itself is unchanged. */
const plainStats = (t: string) => t.replace(/\s*\((?:p\s*[=<>]\s*[\d.]+)[^)]*\)/g, "");
const meaning = (caption: string) => caption.replace(/^How to read this:\s*/i, "");

/** A chart's own title, in plain words and sentence case: no internal column name ever reaches the heading. */
const chartHeading = (title: string, und?: Understanding | null) => {
  const t = humanise(String(title ?? ""), und).replace(/^\s*(total|sum of)\s+/i, "").replace(/\s*\((?:sum|avg|average|mean|count)\)\s*/gi, " ").replace(/\b(Total|Sum of)\s+/g, "").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
};

/** The report as a designed page: the short answer, the key figures, what to do, the evidence, and everything else folded away. */
export default function ReportPage({ run, embedded = false, startChat = false, startPrint = false }: { run: RunResult; embedded?: boolean; startChat?: boolean; startPrint?: boolean }) {
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
  const title = page.company ? `${page.company} report` : "Your report";
  // a seller's key figures: not the number of columns, and "records" rather than "rows"
  const tiles = page.tiles.map((t, i) => ({ ...t, i })).filter((t) => t.label !== "Columns").map((t) => (t.label === "Rows" ? { ...t, label: "Records in your file" } : t));
  const rows = run.profile?.rows;
  const cols = run.profile?.cols;
  return (
    <div className={`report-layout ${startChat ? "with-chat" : ""} ${embedded ? "embedded" : ""}`}>
      <article className="report-page" aria-label="Report">
        <header className="rp-head">
          <div>
            <h1 className="rp-title">{title}</h1>
            <p className="rp-meta">
              <span>{page.date}</span>
              {page.trust && <span className="rp-pill">How sure we are: {page.trust}</span>}
              {page.ai_partly && page.ai_partly.length > 0 && <span className="rp-pill rp-pill-warn" title="The AI's answer for these steps was not used; the rules did them instead.">AI partly unavailable: {page.ai_partly.join(", ")}</span>}
            </p>
          </div>
          {embedded && <div className="rp-buttons no-print">
            <PrintPanel run={run} startOpen={startPrint} />
          </div>}
        </header>

        <TabViewBar tab="report" />
        <Sections tab="report" selfTitled={["report:actions", "report:evidence", "report:behind"]} nodes={{
          "report:short": (
          <section className="rp-headline" data-kind="summary" aria-label="The short answer">
          <p className="rp-kicker">The short answer</p>
          <p className="rp-big">{humanise(plainStats(page.headline), run.understanding)}</p>
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
                    const { title: t, why } = splitAction(a.text);
                    return { id: `report:action:${i}`, node: (
                      <ViewItem key={i} id={`report:action:${i}`} tag="li" className="rp-action" data-kind="action">
                        <span className="rp-num" aria-hidden>{i + 1}</span>
                        <div>
                          <h3><ViewTitle id={`report:action:${i}`} text={humanise(t, run.understanding)} /></h3>
                          {why && !short && <p>{humanise(why, run.understanding)}</p>}
                          {a.figure && !short && <p className="rp-support" data-kind="evidence">{humanise(a.figure.replace(/^Supported by:\s*/i, ""), run.understanding)}</p>}
                        </div>
                      </ViewItem>) };
                  })} />
                </ol>
              ) : (
                <p className="muted">{page.no_action[0] ?? "The data does not support a specific change yet."}</p>
              )}
            </section>
          ),
          "report:evidence": (
            page.charts.length > 0 ? (
              <section className="rp-section" data-kind="charts" aria-labelledby="rp-evidence">
                <ViewTitle id="report:evidence" tag="h2" text="The evidence" />
                <div className="grid">
                  <ShowMore tab="report" items={page.charts.slice(0, 6).map((c) => {
                    const cid = `report:chart:${c.path.split("/").pop()!.replace(/\.\w+$/, "")}`;
                    return { id: cid, node: (
                      <ViewItem key={c.path} id={cid} tag="article" className="rp-chart surface span-6" data-kind="chart">
                        <h3><ViewTitle id={cid} text={chartHeading(c.title, run.understanding)} /></h3>
                        <ViewImg id={cid} src={c.url ?? ""} alt={c.title} />
                        <figcaption data-kind="takeaway"><strong>What this means:</strong> {humanise(meaning(c.caption), run.understanding)}</figcaption>
                        {c.details.length > 0 && (
                          <details className="fold fold-working">
                            <summary>How we got this</summary>
                            <ul>{c.details.map((d, i) => <li key={i}>{humanise(d, run.understanding)}</li>)}</ul>
                          </details>
                        )}
                      </ViewItem>) };
                  })} />
                </div>
              </section>
            ) : <></>
          ),
          "report:behind": (
            <section className="rp-section behind" data-kind="working" aria-label="How we got this">
              <details className="fold fold-working">
                <summary>
                  <ViewTitle id="report:behind" text="How we got this" />
                  <span className="fold-hint">the working: how the data was set up, what we cleaned, how each figure was calculated</span>
                </summary>
                <Md md={page.behind_md} />
              </details>
            </section>
          ),
        }} />
        <ShowAllLink tab="report" />

        <LensPins target="report" />

        <footer className="rp-foot">
          <span>Mode: {page.mode}</span>
          {rows ? <span>{rows.toLocaleString()} rows, {cols} columns</span> : null}
          <span>Made by Analyst Studio on {page.date}</span>
        </footer>
      </article>
      {startChat && (
        <aside className="lens-embedded no-print" aria-label="Lens, ask your data">
          <LensProvider runId={run.run_id}><LensChat variant="embedded" /></LensProvider>
        </aside>
      )}
    </div>
  );
}
