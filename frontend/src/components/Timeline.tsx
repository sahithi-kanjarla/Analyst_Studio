import { useEffect, useRef } from "react";
import { AgentEvent } from "../api";

const COLORS: Record<string, string> = {
  Profiler: "#6B675E",
  Joiner: "#6B675E",
  Planner: "#6B675E",
  Cleaner: "#6B675E",
  Analyst: "#6B675E",
  Critic: "#6B675E",
  Insights: "#6B675E",
  Strategist: "#6B675E",
  Reporter: "#6B675E",
  "Power BI": "#6B675E",
  System: "#6B675E",
};

export default function Timeline({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (running) end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });      // follow the run while it works; a finished or sample timeline never moves the page
  }, [events.length, running]);

  return (
    <div className="timeline">
      {events.map((e, i) => (
        <div className="event" key={i}>
          <span className="badge" style={{ background: COLORS[e.agent] ?? "#6B7280" }}>
            {e.agent}
          </span>
          <span className="event-msg">{e.message}</span>
        </div>
      ))}
      {running && (
        <div className="event">
          <span className="spinner" /> <span className="muted">Agents are working…</span>
        </div>
      )}
      <div ref={end} />
    </div>
  );
}
