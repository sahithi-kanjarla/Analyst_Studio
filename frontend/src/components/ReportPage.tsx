import { RunResult } from "../api";
import LensChat from "./lens/LensChat";
import { LensProvider } from "./lens/LensContext";
import LensPins from "./lens/LensPins";
import Md from "./Md";
import PrintPanel from "./PrintPanel";
import { ReportBody } from "./Results";

/** "Put more behind what works: Segment = Enterprise: Grow overall revenue…" -> a short title and one line of why. */
function splitAction(text: string): { title: string; why: string } {
  const i = text.indexOf(":");
  const j = text.lastIndexOf(":");
  if (i === -1) return { title: text, why: "" };
  return { title: text.slice(0, i).trim(), why: text.slice(j + 1).trim() };
}

/** Display only: the statistic in brackets is left to "Behind the numbers", the sentence itself is unchanged. */
const plainStats = (t: string) => t.replace(/\s*\((?:p\s*[=<>]\s*[\d.]+)[^)]*\)/g, "");
const meaning = (caption: string) => caption.replace(/^How to read this:\s*/i, "");

/** The report as a designed page: the short answer, the key figures, what to do, the evidence, and everything else folded away. */
export default function ReportPage({ run, embedded = false, startChat = false, startPrint = false }: { run: RunResult; embedded?: boolean; startChat?: boolean; startPrint?: boolean }) {
  const page = run.report_view?.page;
  if (!page) {
    return (
      <>
        <div className="rp-actions no-print"><PrintPanel run={run} /></div>
        <ReportBody md={run.report_md ?? ""} />
        <LensPins target="report" />
      </>
    );
  }
  const title = page.company ? `${page.company} report` : "Your report";
  // a seller's key figures: not the number of columns, and "records" rather than "rows"
  const tiles = page.tiles.filter((t) => t.label !== "Columns").map((t) => (t.label === "Rows" ? { ...t, label: "Records in your file" } : t));
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
            </p>
          </div>
          <div className="rp-buttons no-print">
            <PrintPanel run={run} startOpen={startPrint} />
          </div>
        </header>

        <section className="rp-headline" data-kind="summary" aria-label="The short answer">
          <p className="rp-kicker">The short answer</p>
          <p className="rp-big">{plainStats(page.headline)}</p>
          {page.headline_more && <p className="rp-more">{page.headline_more}</p>}
        </section>

        {tiles.length > 0 && (
          <section className="rp-tiles grid" data-kind="numbers" aria-label="Key figures">
            {tiles.map((t) => (
              <div className={`rp-tile ${tiles.length === 3 ? "span-4" : "span-3"}`} key={t.label}>
                <span className={`rp-tile-value ${t.value.length > 11 ? "long" : ""}`}>{t.value}</span>
                <span className="rp-tile-label">{t.label}</span>
              </div>
            ))}
          </section>
        )}

        <section className="rp-section" data-kind="summary" aria-labelledby="rp-todo">
          <h2 id="rp-todo">What to do</h2>
          {page.actions.length > 0 ? (
            <ol className="rp-todo">
              {page.actions.map((a, i) => {
                const { title: t, why } = splitAction(a.text);
                return (
                  <li className="rp-action" key={i}>
                    <span className="rp-num" aria-hidden>{i + 1}</span>
                    <div>
                      <h3>{t}</h3>
                      {why && <p>{why}</p>}
                      {a.figure && <p className="rp-support">{a.figure.replace(/^Supported by:\s*/i, "")}</p>}
                    </div>
                  </li>
                );
              })}
            </ol>
          ) : (
            <p className="muted">{page.no_action[0] ?? "The data does not support a specific change yet."}</p>
          )}
        </section>

        {page.charts.length > 0 && (
          <section className="rp-section" data-kind="charts" aria-labelledby="rp-evidence">
            <h2 id="rp-evidence">The evidence</h2>
            <div className="grid">
              {page.charts.slice(0, 6).map((c) => (
                <figure className="rp-chart surface span-6" key={c.path}>
                  <h3>{c.title.charAt(0).toUpperCase() + c.title.slice(1)}</h3>
                  <img src={c.url} alt={c.title} loading="lazy" />
                  <figcaption><strong>What this means:</strong> {meaning(c.caption)}</figcaption>
                  {c.details.length > 0 && (
                    <details>
                      <summary>Details</summary>
                      <ul>{c.details.map((d, i) => <li key={i}>{d}</li>)}</ul>
                    </details>
                  )}
                </figure>
              ))}
            </div>
          </section>
        )}

        <LensPins target="report" />

        <section className="rp-section behind" aria-labelledby="rp-behind">
          <h2 id="rp-behind">Behind the numbers</h2>
          <p className="muted read">Everything we worked out to get here: how the data was set up, what we cleaned, how things were calculated. Open any part you want to check.</p>
          <Md md={page.behind_md} />
        </section>

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
