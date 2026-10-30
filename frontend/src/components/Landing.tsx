import { useEffect, useRef, useState } from "react";
import { api, RunResult } from "../api";
import BIDashboard from "./BIDashboard";
import DataViewer from "./DataViewer";
import Tiers from "./Tiers";
import { CleaningLog, PlanView, ReportBody } from "./Results";

/** The app's real screens. Each one is the same component the results page uses, running on the bundled sample run. */
export type TourTab = "dashboard" | "insights" | "report" | "data" | "cleaning" | "plan";
export const TOUR: { id: TourTab; label: string; line: string }[] = [
  { id: "dashboard", label: "Dashboard", line: "The answer to your question first, then the charts that support it." },
  { id: "insights", label: "Insights", line: "Each finding with how far to trust it, and the actions it supports." },
  { id: "report", label: "Report", line: "A short report you can print or save as PDF. The detail stays one click away." },
  { id: "data", label: "Data", line: "The cleaned table the numbers come from, searchable and sortable." },
  { id: "cleaning", label: "Cleaning log", line: "Every change made to the data, with the rows or values it touched." },
  { id: "plan", label: "Plan", line: "The questions the analysis set out to answer, and the checks it ran." },
];

/** The scroll story: each step names the screen that shows it. */
const STEPS: { tab: TourTab; title: string; text: string }[] = [
  { tab: "data", title: "Upload", text: "Drop in a CSV, Excel or JSON export from your shop, marketplace or accounting tool. Related files are linked automatically." },
  { tab: "plan", title: "Answer a few questions", text: "The app asks at most three short questions, and only where the answer changes what a number means. Skip them and it uses sensible defaults, and says so." },
  { tab: "dashboard", title: "Analysis", text: "Code cleans the data, logging every change, and runs each test. The statistics use every row, not a sample." },
  { tab: "report", title: "A short report", text: "One answer to your question, a few key numbers, and the charts that support them. Everything else is collapsed." },
  { tab: "insights", title: "Actions", text: "Each action names what to do and the finding behind it. A finding that is not reliable does not become an action." },
  { tab: "report", title: "Print or save as PDF", text: "Choose the sections, the title, the paper size and the orientation. Charts are never split across pages." },
];

function sampleScreen(tab: TourTab, run: RunResult) {
  switch (tab) {
    case "dashboard":
      return <BIDashboard run={run} />;
    case "insights":
      return run.report_view ? <Tiers view={run.report_view} goalAnswer={run.goal_answer} /> : null;
    case "report":
      return <ReportBody md={run.report_md ?? ""} />;
    case "data":
      return <DataViewer runId={run.run_id} />;
    case "cleaning":
      return <CleaningLog run={run} />;
    case "plan":
      return <PlanView run={run} />;
  }
}

/** Tabs above a framed screen. Keyboard: arrow keys move between tabs; the tab row scrolls sideways on a phone. */
function Tour({ run, step }: { run: RunResult; step: number }) {
  const [tab, setTab] = useState<TourTab>(STEPS[step].tab);
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  // A visitor's own click overrides the scroll until the next step is reached: the screen follows the step again only when the step changes.
  useEffect(() => setTab(STEPS[step].tab), [step]);
  const current = TOUR.find((t) => t.id === tab) ?? TOUR[0];

  function onKey(e: React.KeyboardEvent, index: number) {
    const move = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!move) return;
    e.preventDefault();
    const next = TOUR[(index + move + TOUR.length) % TOUR.length];
    setTab(next.id);
    tabRefs.current[next.id]?.focus();
  }

  return (
    <div className="tour">
      <div className="tour-tabs" role="tablist" aria-label="Screens of the app">
        {TOUR.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              tabRefs.current[t.id] = el;
            }}
            role="tab"
            id={`tour-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="tour-screen"
            tabIndex={tab === t.id ? 0 : -1}
            className={`tour-tab ${tab === t.id ? "tour-tab-on" : ""}`}
            onClick={() => setTab(t.id)}
            onKeyDown={(e) => onKey(e, i)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="tour-frame">
        <div className="tour-screen" role="tabpanel" id="tour-screen" aria-labelledby={`tour-${tab}`} key={tab}>
          {sampleScreen(tab, run)}
        </div>
      </div>
      <p className="tour-line">{current.line}</p>
    </div>
  );
}

/** Steps that read as the visitor scrolls. The screen follows the step in view, unless a tab was clicked (the next step resets that). */
function Story({ onStep }: { onStep: (index: number) => void }) {
  const refs = useRef<(HTMLLIElement | null)[]>([]);
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) onStep(refs.current.indexOf(e.target as HTMLLIElement));
        }
      },
      { rootMargin: "-40% 0px -40% 0px" },
    );
    refs.current.forEach((el) => el && io.observe(el));
    return () => io.disconnect();
  }, [onStep]);
  return (
    <ol className="story" aria-label="How it works">
      {STEPS.map((s, i) => (
        <li key={i} className="story-step" ref={(el) => {
          refs.current[i] = el;
        }}>
          <span className="story-n">{i + 1}</span>
          <h3>{s.title}</h3>
          <p>{s.text}</p>
        </li>
      ))}
    </ol>
  );
}

/** The product section: the tour and the scroll story, both on the bundled sample run. No upload and no AI call. */
export function ProductSection() {
  const [run, setRun] = useState<RunResult | null>(null);
  const [error, setError] = useState("");
  const [step, setStep] = useState(0);
  useEffect(() => {
    api.openDemo().then((d) => api.getRun(d.run_id)).then(setRun).catch(() => setError("The sample could not be loaded. Upload your own file to start."));
  }, []);
  return (
    <section className="product" aria-labelledby="product-title">
      <h2 id="product-title" className="product-title">What you see when it finishes</h2>
      <p className="product-sub">Every screen below is the real app, running on a sample store's sales. Click through them; nothing is uploaded and no AI is called.</p>
      <div className="product-grid">
        <Story onStep={setStep} />
        <div className="product-screen">
          {error && <div className="error">{error}</div>}
          {run ? <Tour run={run} step={step} /> : !error && <div className="tour-loading" aria-live="polite">Loading the sample…</div>}
        </div>
      </div>
    </section>
  );
}
