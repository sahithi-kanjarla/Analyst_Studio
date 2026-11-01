import { useEffect, useState } from "react";
import { AgentEvent, api, RunResult } from "../api";
import Timeline from "./Timeline";
import Results from "./Results";
import AgentFlow from "./AgentFlow";
import QuestionForm from "./QuestionForm";
import { LensProvider } from "./lens/LensContext";
import { ViewProvider } from "./view/ViewContext";
import LensDock from "./lens/LensDock";

export default function RunView({ runId }: { runId: string }) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [run, setRun] = useState<RunResult | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0); // bumped after answers are sent so the stream is reopened for the resumed run

  useEffect(() => {
    let closed = false;
    setEvents([]);
    setRun(null);
    const es = new EventSource(api.eventsUrl(runId));
    es.onmessage = (m) => setEvents((prev) => [...prev, JSON.parse(m.data) as AgentEvent]);
    const finish = async () => {
      es.close();
      try {
        const r = await api.getRun(runId);
        if (!closed) setRun(r);
      } catch (e) {
        if (!closed) setError(e instanceof Error ? e.message : "Could not load the result");
      }
    };
    es.addEventListener("end", finish);
    es.onerror = () => {
      // The stream closes itself when a run ends; only treat it as a failure if the run is still unknown.
      api.getRun(runId).then((r) => {
        if (r.status === "done" || r.status === "error" || r.status === "needs_input") finish();
      }).catch(() => setError("Lost connection to the backend."));
    };
    return () => {
      closed = true;
      es.close();
    };
  }, [runId, attempt]);

  const running = !run && !error;

  // Keep the panel open while the agents work, then tuck it away so the dashboard gets the full width.
  const [sideOpen, setSideOpen] = useState(true);
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (!running && !touched) setSideOpen(false);
  }, [running, touched]);
  const toggleSide = () => {
    setTouched(true);
    setSideOpen((o) => !o);
  };

  return (
    <div className={`runview ${sideOpen ? "" : "side-closed"}`}>
      <button className="side-toggle" onClick={toggleSide} aria-expanded={sideOpen} aria-label={sideOpen ? "Hide agent activity" : "Show agent activity"} title={sideOpen ? "Hide agent activity" : "Show agent activity"}>
        ☰ <span>{sideOpen ? "Hide agent activity" : "Agent activity"}</span>
      </button>
      {sideOpen && (
        <aside className="card side">
          <h3>Agent activity</h3>
          <Timeline events={events} running={running} />
        </aside>
      )}
      <section className="main">
        {error && <div className="error">{error}</div>}
        {running && (
          <div className="card placeholder">
            <div className="orb" />
            <h2>Analysing your data…</h2>
            <p className="muted">Results appear here as soon as the Critic approves them. You can leave this tab open; the run continues on the server.</p>
            <div className="bar-indet" />
            <AgentFlow events={events} running compact />
          </div>
        )}
        {run?.status === "needs_input" && <QuestionForm runId={runId} onSubmitted={() => setAttempt((a) => a + 1)} />}
        {run?.status === "error" && <div className="error">The analysis failed: {run.error}</div>}
        {run?.status === "done" && (
          <ViewProvider runId={runId}>
            <LensProvider runId={runId}>
              <Results run={run} />
              <LensDock />
            </LensProvider>
          </ViewProvider>
        )}
      </section>
    </div>
  );
}
