import { useEffect, useState } from "react";
import { AgentEvent, api, RunResult } from "../api";
import Timeline from "./Timeline";
import Results from "./Results";

export default function RunView({ runId }: { runId: string }) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [run, setRun] = useState<RunResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let closed = false;
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
        if (r.status === "done" || r.status === "error") finish();
      }).catch(() => setError("Lost connection to the backend."));
    };
    return () => {
      closed = true;
      es.close();
    };
  }, [runId]);

  const running = !run && !error;
  return (
    <div className="runview">
      <aside className="card side">
        <h3>Agent activity</h3>
        <Timeline events={events} running={running} />
      </aside>
      <section className="main">
        {error && <div className="error">{error}</div>}
        {running && (
          <div className="card placeholder">
            <h2>Analysing…</h2>
            <p className="muted">Results appear here as soon as the Critic approves them. You can leave this tab open; the run continues on the server.</p>
          </div>
        )}
        {run?.status === "error" && <div className="error">The analysis failed: {run.error}</div>}
        {run?.status === "done" && <Results run={run} />}
      </section>
    </div>
  );
}
