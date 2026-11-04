import { useEffect, useRef, useState } from "react";
import { AgentEvent, api, RunResult } from "../api";
import { DATA_LABEL, DATA_VIEWS, NAV_LABEL, NAV_TABS, NavTab, Tab, TAB_LINE } from "../tabs";
import Dialog from "./Dialog";
import Logo from "./Logo";
import { TabBody } from "./Results";
import ReportPage from "./ReportPage";
import Timeline from "./Timeline";
import UploadForm from "./UploadForm";

/** A screen of the app: one of its tabs, or the report with the chat or the print panel open. */
export type SampleView = Tab | "ask" | "print";
const TOUR_TABS: NavTab[] = NAV_TABS;          // the five real tabs, in the app's order
const isDataView = (v: SampleView) => (DATA_VIEWS as string[]).includes(v);
const tabOf = (v: SampleView): Tab => (v === "ask" || v === "print" ? "report" : v);                 // the screen shown (for its one-line description)
const navOf = (v: SampleView): NavTab => (isDataView(v) ? "data" : v === "ask" || v === "print" ? "report" : (v as NavTab));          // the tab it belongs to

const STORY: { view: SampleView; title: string; text: string }[] = [
  { view: "plan", title: "It asks before it assumes", text: "When something is unclear, like two date columns or a value such as 999 that may mean “missing”, it asks you a short question. It never guesses quietly." },
  { view: "data", title: "Every figure is calculated by code", text: "The AI helps read your columns and word the report. The numbers come from code, on every row of your file, so you can check them in your own data." },
  { view: "report", title: "Only what matters", text: "You get the short answer, a few key figures and what to do. Tables, cleaning notes and settings stay folded away until you want them." },
  { view: "ask", title: "Ask Lens about your data", text: "Pick a suggested question, such as which segment earns the most. The answer, the chart and how it was worked out all come from your data, never from the AI." },
  { view: "print", title: "Print or save as PDF", text: "Choose what to include, add a title and a note, and pick the paper size. Charts and cards never split across two pages." },
];

/** Renders its children at a fixed design width and zooms them down to fit a narrower window (not on a phone, where they simply reflow). */
function Scaled({ children, design = 1040, height }: { children: React.ReactNode; design?: number; height: number | string }) {
  const box = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth;
      setZoom(w >= 520 && w < design ? w / design : 1);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [design]);
  return (
    <div className="scaled" ref={box} style={{ height }}>
      <div style={zoom !== 1 ? { zoom, width: design } : undefined}>{children}</div>
    </div>
  );
}

function SampleWindow({ run, view, title, onPick }: { run: RunResult; view: SampleView; title: string; onPick: (v: Tab) => void }) {
  return (
    <div className="window" role="group" aria-label="The app, running on a sample report">
      <div className="window-bar"><span className="window-dots" aria-hidden><i /><i /><i /></span><span className="window-title">{title}</span></div>
      <Scaled height="min(60vh, 560px)" key={view}>
        <div className="window-body">
          {isDataView(view) && (
            <div className="dv" role="tablist" aria-label="Data views">
              {DATA_VIEWS.map((v) => <button key={v} role="tab" type="button" aria-selected={view === v} className={`dv-item ${view === v ? "dv-on" : ""}`} onClick={() => onPick(v)}>{DATA_LABEL[v]}</button>)}
            </div>
          )}
          {view === "ask" ? <ReportPage run={run} embedded startChat /> : view === "print" ? <ReportPage run={run} embedded startPrint /> : <TabBody run={run} tab={view} embedded />}
        </div>
      </Scaled>
    </div>
  );
}

function Tabs({ view, onPick }: { view: SampleView; onPick: (v: Tab) => void }) {          // a click on a tab opens its first screen
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const current = navOf(view);
  function onKey(e: React.KeyboardEvent, i: number) {
    const move = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!move) return;
    e.preventDefault();
    const next = TOUR_TABS[(i + move + TOUR_TABS.length) % TOUR_TABS.length];
    onPick(next);
    refs.current[next]?.focus();
  }
  return (
    <div className="tour-tabs" role="tablist" aria-label="Screens of the app">
      {TOUR_TABS.map((t, i) => (
        <button key={t} ref={(el) => { refs.current[t] = el; }} role="tab" id={`tour-${t}`} aria-selected={current === t} aria-controls="tour-window" tabIndex={current === t ? 0 : -1}
          className={`tour-tab ${current === t ? "tour-tab-on" : ""}`} onClick={() => onPick(t)} onKeyDown={(e) => onKey(e, i)}>
          {NAV_LABEL[t]}
        </button>
      ))}
    </div>
  );
}

function useSampleEvents(runId: string | undefined) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  useEffect(() => {
    if (!runId) return;
    const es = new EventSource(api.eventsUrl(runId));
    es.onmessage = (m) => setEvents((p) => [...p, JSON.parse(m.data) as AgentEvent]);
    es.addEventListener("end", () => es.close());
    es.onerror = () => es.close();
    return () => es.close();
  }, [runId]);
  return events;
}

/** A small, inert look at a real piece of the app. */
function Preview({ children, label }: { children: React.ReactNode; label: string }) {
  return <div className="preview" role="img" aria-label={label}><div className="preview-in" aria-hidden>{children}</div></div>;
}

function HowItWorks({ run }: { run: RunResult | null }) {
  const events = useSampleEvents(run?.run_id);
  const steps = [
    { title: "Upload", text: "Drop in your sales file. CSV, Excel and JSON all work.",
      preview: <div className="drop drop-has"><span className="up"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 16V4M7 9l5-5 5 5M5 20h14" /></svg></span><strong>Drag files here or click to browse</strong></div> },
    { title: "Answer a few simple questions", text: "Tell us what you want to know. We ask only when an answer changes a number.",
      preview: run?.plan ? <div className="plan"><h4>Questions we set out to answer</h4><ul>{run.plan.questions.slice(0, 2).map((q) => <li key={q}>{q}</li>)}</ul></div> : null },
    { title: "We analyse", text: "The AI suggests what to look at. Code does every calculation.",
      preview: <Timeline events={events.slice(0, 6)} running={false} /> },
    { title: "Get a short report", text: "The answer, the key figures and what to do, on one page.",
      preview: run ? <ReportPage run={run} embedded /> : null },
  ];
  return (
    <section className="how" aria-labelledby="how-title">
      <h2 id="how-title">How it works</h2>
      <ol className="how-flow">
        {steps.map((s, i) => (
          <li key={s.title}>
            <span className="how-n">{i + 1}</span>
            <h3>{s.title}</h3>
            <p>{s.text}</p>
            <Preview label={`Preview: ${s.title}`}>{s.preview}</Preview>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Story({ onView, run, view }: { onView: (v: SampleView) => void; run: RunResult; view: SampleView }) {
  const refs = useRef<(HTMLLIElement | null)[]>([]);
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) onView(STORY[refs.current.indexOf(e.target as HTMLLIElement)].view);
    }, { rootMargin: "-40% 0px -40% 0px" });
    refs.current.forEach((el) => el && io.observe(el));
    return () => io.disconnect();
  }, [onView]);
  return (
    <section className="story" aria-label="What makes it different">
      <div className="story-window"><SampleWindow run={run} view={view} title="Sample Store" onPick={onView} /></div>
      <ol className="story-steps">
        {STORY.map((s, i) => (
          <li key={s.title} ref={(el) => { refs.current[i] = el; }} className={view === s.view ? "story-on" : ""}>
            <h3>{s.title}</h3>
            <p>{s.text}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function StartPage({ onOpenRun, openNew = false }: { onOpenRun: (id: string) => void; openNew?: boolean }) {
  const [run, setRun] = useState<RunResult | null>(null);
  const [view, setView] = useState<SampleView>("report");
  const [dialog, setDialog] = useState(openNew);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setDialog(openNew);
  }, [openNew]);
  useEffect(() => {
    api.openDemo().then((d) => api.getRun(d.run_id)).then(setRun).catch(() => setError("The sample could not be loaded."));
  }, []);
  async function sample() {
    setBusy(true);
    try { onOpenRun((await api.openDemo()).run_id); } catch { setError("The sample could not be opened."); setBusy(false); }
  }
  const buttons = (
    <div className="hero-buttons">
      <button className="btn-primary btn-lg" onClick={() => setDialog(true)}>Try it yourself</button>
      <button className="btn btn-lg" disabled={busy} onClick={sample}>Try with sample data</button>
    </div>
  );
  return (
    <div className="start">
      <header className="hero">
        <Logo size={56} title="Analyst Studio" />
        <h1>Know your sales. Trust every number.</h1>
        <p className="hero-sub">Upload a sales file, answer a few simple questions, and get a short report that says what to do next.</p>
        {buttons}
        {error && <div className="error">{error}</div>}
      </header>

      <section className="tour" aria-label="The app, on a sample report">
        <Tabs view={view} onPick={setView} />
        {run ? <div id="tour-window" role="tabpanel" aria-labelledby={`tour-${navOf(view)}`}><SampleWindow run={run} view={view} title="Sample Store" onPick={setView} /></div> : <div className="window window-loading" aria-live="polite">Loading the sample…</div>}
        <p className="tour-line">{TAB_LINE[tabOf(view)]}</p>
      </section>

      <HowItWorks run={run} />
      {run && <Story run={run} view={view} onView={setView} />}

      <section className="trust" aria-labelledby="trust-title">
        <h2 id="trust-title" className="sr-only">Why you can trust it</h2>
        <div><h3>The AI suggests. Code calculates.</h3><p>The AI helps read your columns and word the report. Every number comes from code that you can check against your own file.</p></div>
        <div><h3>Nothing is changed in secret.</h3><p>Every fix to your data is written down. When something is unclear, we ask you instead of guessing.</p></div>
        <div><h3>Your file stays on this computer.</h3><p>Runs are stored here and deleted after 30 days. If an AI is used, it sees summaries, never your raw rows.</p></div>
      </section>

      <section className="limits" aria-labelledby="limits-title">
        <h2 id="limits-title">What it can and can’t do</h2>
        <div className="limits-cols">
          <div>
            <h3>It can</h3>
            <ul>
              <li>Read CSV, Excel and JSON files of up to 500,000 rows.</li>
              <li>Link several related files together.</li>
              <li>Answer your question and say how sure it is.</li>
              <li>Show you the data and every change made to it.</li>
            </ul>
          </div>
          <div>
            <h3>It can’t</h3>
            <ul>
              <li>Prove that one thing causes another. It shows patterns, not causes.</li>
              <li>Handle very large files, or filter its charts like Power BI does.</li>
              <li>Keep separate accounts. It is made for one person on one computer.</li>
              <li>Be perfect with ambiguous dates. It tells you what it assumed.</li>
            </ul>
          </div>
        </div>
      </section>

      <section className="closing" aria-labelledby="closing-title">
        <h2 id="closing-title">See it on your own numbers.</h2>
        {buttons}
      </section>

      {dialog && (
        <Dialog title="Try it yourself" intro="Add your file and tell us what you want to find out." onClose={() => { setDialog(false); if (window.location.hash === "#/new") window.location.hash = "#/"; }}>
          <UploadForm onStarted={onOpenRun} />
        </Dialog>
      )}
    </div>
  );
}
