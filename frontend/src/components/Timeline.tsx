import { useEffect, useRef } from "react";
import { AgentEvent } from "../api";

const COLORS: Record<string, string> = {
  Profiler: "#3FA9F5",
  Joiner: "#D6609B",
  Planner: "#8E6BEE",
  Cleaner: "#22A699",
  Analyst: "#4F6BED",
  Critic: "#E5484D",
  Insights: "#0EA5A4",
  Strategist: "#B45309",
  Reporter: "#F2994A",
  "Power BI": "#C9A400",
  System: "#6B7280",
};

export default function Timeline({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [events.length]);

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
