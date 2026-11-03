import { useEffect, useState } from "react";
import { RunResult } from "../api";
import Dialog from "./Dialog";
import { useView } from "./view/ViewContext";

/** What the printed report includes. The main view (summary, key numbers, charts) is on by default; the rest is opt-in. */
export interface PrintOptions {
  summary: boolean;
  numbers: boolean;
  charts: boolean;
  tables: boolean;
  setup: boolean; // data-quality notes and how the data was set up
  details: boolean; // evidence and the other collapsed sections
  title: string;
  company: string;
  date: string;
  note: string;
  paper: "A4" | "Letter";
  orientation: "portrait" | "landscape";
  colour: "colour" | "bw";
}

const KEY = "analyst-studio.print-options.v1";
const DEFAULTS: Omit<PrintOptions, "title" | "company" | "date"> = {
  summary: true, numbers: true, charts: true, tables: false, setup: false, details: false, note: "", paper: "A4", orientation: "portrait", colour: "colour",
};

/** Last choices, remembered in this browser only. Storage can be blocked, so every access is guarded. */
function loadOptions(run: RunResult): PrintOptions {
  const today = new Date().toISOString().slice(0, 10);
  const base: PrintOptions = { ...DEFAULTS, title: "Analysis report", company: run.context?.company ?? "", date: today };
  try {
    const saved = JSON.parse(window.localStorage.getItem(KEY) ?? "null") as Partial<PrintOptions> | null;
    return saved ? { ...base, ...saved, date: today } : base;
  } catch {
    return base;
  }
}

function saveOptions(o: PrintOptions) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(o));
  } catch {
    /* storage blocked: the choices simply are not remembered */
  }
}

/** Tags each part of the rendered report so the print CSS can leave it out: summary, key numbers, charts, tables, setup and details. */
export function tagReport(root: HTMLElement) {
  let kind = "";
  for (const el of Array.from(root.children)) {
    if (el.tagName === "H2") {
      const t = el.textContent ?? "";
      kind = /executive summary/i.test(t) ? "summary" : /key numbers/i.test(t) ? "numbers" : "";
    }
    if (el.tagName === "HR") kind = "";
    if (kind) (el as HTMLElement).dataset.kind = kind;
  }
  root.querySelectorAll("img").forEach((img) => img.setAttribute("data-kind", "charts"));
  root.querySelectorAll("table").forEach((t) => t.setAttribute("data-kind", "tables"));
  root.querySelectorAll("details").forEach((d) => {
    const label = d.querySelector("summary")?.textContent ?? "";
    d.setAttribute("data-kind", /data|clean|setup|quality|set up|checked|definition/i.test(label) ? "setup" : "details");
  });
}

const KIND_OF_SECTION: [keyof PrintOptions, string][] = [["summary", "summary"], ["numbers", "numbers"], ["setup", "setup"], ["details", "details"]];

/** The print options. Inline (a button that opens the panel under it) in the landing tour; with `dialog` it is opened from the Export menu: `open` and `onClose` are the caller's, and the choices still apply to the printed page while it is closed. */
export default function PrintPanel({ run, startOpen = false, dialog = false, open: openProp = false, onClose }: { run: RunResult; startOpen?: boolean; dialog?: boolean; open?: boolean; onClose?: () => void }) {
  const [openInline, setOpen] = useState(startOpen);
  const open = dialog ? openProp : openInline;
  const view = useView();
  const hiddenItems = (view?.data.state.hidden ?? []).filter((id) => view?.data.items[id]);
  const [o, setO] = useState<PrintOptions>(() => loadOptions(run));
  const set = <K extends keyof PrintOptions>(k: K, v: PrintOptions[K]) => {
    setO((prev) => {
      const next = { ...prev, [k]: v };
      saveOptions(next);
      return next;
    });
  };

  // Apply the choices to the page: classes the print CSS reads, and the page size.
  useEffect(() => {
    const body = document.body;
    const report = document.querySelector<HTMLElement>(".report-page");
    if (report) tagReport(report);
    body.classList.toggle("print-no-charts", !o.charts);
    body.classList.toggle("print-no-tables", !o.tables);
    body.classList.toggle("print-bw", o.colour === "bw");
    for (const [opt, kind] of KIND_OF_SECTION) body.classList.toggle(`print-no-${kind}`, !o[opt]);
    let style = document.getElementById("print-page") as HTMLStyleElement | null;
    if (!style) {
      style = document.createElement("style");
      style.id = "print-page";
      document.head.appendChild(style);
    }
    style.textContent = `@page { size: ${o.paper} ${o.orientation}; margin: 14mm 14mm 16mm; }`;
  }, [o, open]);

  // Collapsed sections are printed open. Reverted after printing so the screen is unchanged.
  useEffect(() => {
    const before: [HTMLDetailsElement, boolean][] = [];
    const openAll = () => {
      document.querySelectorAll<HTMLDetailsElement>(".report-page details").forEach((d) => {
        before.push([d, d.open]);
        d.open = true; // the print CSS hides the sections that are not chosen
      });
    };
    const restore = () => {
      before.forEach(([d, was]) => (d.open = was));
      before.length = 0;
    };
    window.addEventListener("beforeprint", openAll);
    window.addEventListener("afterprint", restore);
    return () => {
      window.removeEventListener("beforeprint", openAll);
      window.removeEventListener("afterprint", restore);
    };
  }, []);

  const check = (k: keyof PrintOptions, label: string, hint?: string) => (
    <label className="print-check">
      <input type="checkbox" checked={!!o[k]} onChange={(e) => set(k, e.target.checked as never)} />
      <span>{label}{hint && <small>{hint}</small>}</span>
    </label>
  );

  const panel = (
      <section id="print-panel" className="print-panel" aria-label="Print options">
          <fieldset>
            <legend>Include</legend>
            {check("summary", "Summary and actions", "the answer and what to do")}
            {check("numbers", "Key numbers")}
            {check("charts", "Charts")}
            {check("tables", "Tables")}
            {check("setup", "Data-quality notes and how the data was set up")}
            {check("details", "Evidence and other details")}
          </fieldset>
          {view && hiddenItems.length > 0 && (
            <fieldset>
              <legend>Hidden items (optional)</legend>
              <p className="muted small">You hid these on screen. They are still in your saved report; tick any to include it in the printout.</p>
              {hiddenItems.map((id) => (
                <label className="print-check" key={id}>
                  <input type="checkbox" checked={view.printSet.has(id)} onChange={() => view.togglePrint(id)} />
                  <span>{view.title(id, view.data.items[id].title)}<small>{view.data.items[id].kind}</small></span>
                </label>
              ))}
            </fieldset>
          )}
          <fieldset>
            <legend>Page</legend>
            <label className="print-field">Title <input value={o.title} onChange={(e) => set("title", e.target.value)} /></label>
            <label className="print-field">Company <input value={o.company} onChange={(e) => set("company", e.target.value)} /></label>
            <label className="print-field">Date <input type="date" value={o.date} onChange={(e) => set("date", e.target.value)} /></label>
            <label className="print-field">Note at the top <textarea rows={2} value={o.note} onChange={(e) => set("note", e.target.value)} /></label>
            <div className="print-row">
              <label className="print-field">Paper
                <select value={o.paper} onChange={(e) => set("paper", e.target.value as PrintOptions["paper"])}><option value="A4">A4</option><option value="Letter">Letter</option></select>
              </label>
              <label className="print-field">Orientation
                <select value={o.orientation} onChange={(e) => set("orientation", e.target.value as PrintOptions["orientation"])}><option value="portrait">Portrait</option><option value="landscape">Landscape</option></select>
              </label>
              <label className="print-field">Colour
                <select value={o.colour} onChange={(e) => set("colour", e.target.value as PrintOptions["colour"])}><option value="colour">Colour</option><option value="bw">Black and white</option></select>
              </label>
            </div>
          </fieldset>
          <div className="print-actions">
            <button className="primary" onClick={() => window.print()}>Print / Save as PDF</button>
            <span className="muted small">In the print dialog, choose “Save as PDF”. Your choices are remembered in this browser only.</span>
          </div>
      </section>
  );

  return (
    <>
    {dialog ? (
      open && <Dialog title="Print or save as PDF" intro="Choose what to include. In the print dialog, choose “Save as PDF” to keep a file." onClose={() => onClose?.()} wide>{panel}</Dialog>
    ) : (
    <div className="print-wrap no-print">
      <button className="btn" aria-expanded={open} aria-controls="print-panel" onClick={() => setOpen((v) => !v)}>Print / Save as PDF</button>
      {open && panel}
    </div>
    )}
    {/* The header of the printed page: visible only in print (outside the hidden panel wrapper) */}
    <div className="print-header" aria-hidden="true">
      <div className="print-head-title">{o.title}{o.company && <span> · {o.company}</span>}</div>
      <div className="print-head-meta">{o.date}</div>
      {o.note && <p className="print-head-note">{o.note}</p>}
    </div>
    </>
  );
}
