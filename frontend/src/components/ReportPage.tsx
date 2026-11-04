import { ReportCard, RunResult } from "../api";
import { bigNumber, direction, takeaway } from "../insightwidget";
import { humanise, plainLabel, plainTitle, Understanding } from "../display";
import LensChat from "./lens/LensChat";
import { LensProvider } from "./lens/LensContext";
import LensPins from "./lens/LensPins";
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

/** Display only: the statistic in brackets is left out of the page, the sentence itself is unchanged. */
const plainStats = (t: string) => t.replace(/\s*\((?:p\s*[=<>]\s*[\d.]+)[^)]*\)/g, "");
const meaning = (caption: string) => caption.replace(/^How to read this:\s*/i, "");

/** A chart's own title, in plain words and sentence case: no internal column name ever reaches the heading. */
const chartHeading = (title: string, und?: Understanding | null) => {
  const t = humanise(String(title ?? ""), und).replace(/^\s*(total|sum of)\s+/i, "").replace(/\s*\((?:sum|avg|average|mean|count)\)\s*/gi, " ").replace(/\b(Total|Sum of)\s+/g, "").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
};

/** One curated finding in "What matters": a lead figure, a plain title, a small chart, the result in one line and one or two sentences on why it matters. */
function ReportFinding({ c, und }: { c: ReportCard; und?: Understanding | null }) {
  const say = (t: string) => humanise(t, und);
  const cid = `report:finding:${c.id}`;
  const big = bigNumber(c.headline, c.tool);
  const dir = direction(c.headline, c.tool, big?.sign ?? 0);
  const why = takeaway(say(c.why_it_matters), say(c.observation), 190);
  return (
    <ViewItem id={cid} tag="article" className="rp-chart rp-matter" data-kind="chart">
      {big ? (
        <p className={`rp-lead ${dir ? `rp-${dir}` : ""}`}><b>{dir === "up" ? "▲ " : dir === "down" ? "▼ " : ""}{big.value}</b><span>{big.label}</span></p>
      ) : (
        <p className="rp-lead"><span className={`rp-trust rp-trust-${c.confidence.level}`}>{c.confidence.label} confidence</span></p>
      )}
      <h3><ViewTitle id={cid} text={plainTitle(c.title, und)} /></h3>
      <p className="iw-line" data-kind="takeaway">{say(c.headline)}</p>
      {c.chart_url && <ViewImg id={`report:chart:${c.id}`} src={c.chart_url} alt={say(c.title)} />}
      {why && <p className="rp-why" data-kind="reason">{why}</p>}
      {c.merged && c.merged.items.length > 1 && (
        <ul className="muted small">{c.merged.items.map((it) => <li key={it.id}>{say(it.text)}</li>)}</ul>
      )}
    </ViewItem>
  );
}

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
  const tiles = page.tiles.map((t, i) => ({ ...t, i })).filter((t) => t.label !== "Columns").map((t) => (t.label === "Rows" ? { ...t, label: "Records in your file" } : t));

  const reportFindings = run.report_view?.report_findings ?? [];
  const hasFindings = reportFindings.length > 0;
  
  return (
    <div className={`report-layout ${startChat ? "with-chat" : ""} ${embedded ? "embedded" : ""}`}>
      <article className="report-page" aria-label="Report">
        <header className="rp-head">
          <div>
            <h1 className="rp-title">{title}</h1>
            <p className="rp-meta">
              <span>{page.date}</span>
              {page.trust && <span className="rp-pill">How sure we are: {page.trust}</span>}
            </p>
          </div>
          {embedded && <div className="rp-buttons no-print">
            <PrintPanel run={run} startOpen={startPrint} />
          </div>}
        </header>

        {page.scope && <p className="rp-scope" data-kind="scope">{page.scope}</p>}
        {run.report_view?.overview_note && <p className="muted" data-kind="overview-note">{run.report_view.overview_note}</p>}
        <TabViewBar tab="report" />
        <Sections tab="report" selfTitled={["report:actions", "report:evidence"]} nodes={{
          "report:short": (
          <section className="rp-headline" data-kind="summary" aria-label="The short answer">
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
                          {a.figure && !short && <details className="rp-support"><summary>Based on</summary><p data-kind="evidence">{humanise(a.figure.replace(/^Supported by:\s*/i, ""), run.understanding)}</p></details>}
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
