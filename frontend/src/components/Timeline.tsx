import { useEffect, useRef } from "react";
import { AgentEvent } from "../api";
import { MUTED } from "../palette";

// Every stage reads the same: the badge names the stage, it is not a colour code.
const BADGE = MUTED;

export default function Timeline({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (running) end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });      // follow the run while it works; a finished or sample timeline never moves the page
  }, [events.length, running]);

  return (
    <div className="timeline">
      {events.map((e, i) => (
        <div className="event" key={i}>
          <span className="badge" style={{ background: BADGE }}>
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
