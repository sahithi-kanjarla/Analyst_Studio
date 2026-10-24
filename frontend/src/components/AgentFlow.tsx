import { useEffect, useState } from "react";
import { AgentEvent } from "../api";
import { AGENTS, ALIAS, ORDER, PHASES } from "./agents";

type Props = { events?: AgentEvent[]; running?: boolean; compact?: boolean };

/**
 * The agent pipeline as a flowchart. Two uses:
 *  - explain (no events): auto-plays through the agents; click one to read what it does
 *  - live (events given): lights up the agents as the run reaches them
 */
export default function AgentFlow({ events, running = false, compact = false }: Props) {
  const live = events !== undefined;
  const [sel, setSel] = useState(0);
  const [auto, setAuto] = useState(true);

  useEffect(() => {
    if (live || !auto) return;
    const t = setInterval(() => setSel((s) => (s + 1) % AGENTS.length), 2600);
    return () => clearInterval(t);
  }, [live, auto]);

  let lastIdx = -1;
  let maxIdx = -1;
  if (live) {
    for (const e of events!) {
      const i = ORDER.indexOf(ALIAS[e.agent] ?? e.agent);
      if (i < 0) continue;
      lastIdx = i;
      maxIdx = Math.max(maxIdx, i);
    }
  }
  const state = (i: number) => {
    if (!live) return i === sel ? "on" : i < sel ? "past" : "";
    if (i === lastIdx && running) return "on";
    return i <= maxIdx ? "past" : "";
  };

  const cur = AGENTS[sel];

  return (
    <div className={`flow ${compact ? "flow-compact" : ""}`}>
      <div className="flow-grid">
        <div className="flow-end flow-in">
          <b>Your files</b>
          <span>CSV · Excel · JSON</span>
        </div>
        {PHASES.map((p) => (
          <div className="flow-phase" key={p.n}>
            <div className="flow-phase-head">
              <i>{p.n}</i>
              <div>
                <b>{p.title}</b>
                <span>{p.sub}</span>
              </div>
            </div>
            <div className="flow-nodes">
              {AGENTS.map((a, i) =>
                a.phase !== p.n ? null : (
                  <div key={a.id} style={{ display: "contents" }}>
                    <button
                      type="button"
                      className={`node ${state(i) ? "node-" + state(i) : ""}`}
                      onClick={() => {
                        if (live) return;
                        setSel(i);
                        setAuto(false);
                      }}
                      aria-pressed={!live && sel === i}
                      aria-label={`${a.id}: ${a.role}`}
                    >
                      <span className="node-ico">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{a.icon}</svg>
                      </span>
                      <span className="node-txt">
                        <b>{a.id}</b>
                        {!compact && <small>{a.role}</small>}
                      </span>
                      {state(i) === "past" && <em className="node-tick" aria-hidden>✓</em>}
                      {state(i) === "on" && live && <em className="node-spin" aria-hidden />}
                    </button>
                    {i < AGENTS.length - 1 && AGENTS[i + 1].phase === p.n && <span className={`wire ${state(i) === "past" || state(i) === "on" ? "wire-on" : ""}`} aria-hidden />}
                  </div>
                ),
              )}
            </div>
            {p.n === 3 && <div className="loop" title="If a result looks fragile the Critic asks for more checks">↺ Critic can send the Analyst back for robustness checks</div>}
          </div>
        ))}
        <div className="flow-end flow-out">
          <b>Decisions</b>
          <span>Report · Strategy · Power BI</span>
        </div>
      </div>

      {!live && !compact && (
        <div className="flow-detail" key={cur.id} aria-live="polite">
          <div className="fd-head">
            <span className="fd-ico">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{cur.icon}</svg>
            </span>
            <div>
              <b>{cur.id}</b>
              <span>{cur.role}</span>
            </div>
            <span className={`tag ${cur.brain === "code" ? "tag-code" : "tag-llm"}`}>{cur.brain === "code" ? "Pure Python" : "LLM optional"}</span>
            <button type="button" className="fd-play" onClick={() => setAuto((a) => !a)} aria-label={auto ? "Pause the tour" : "Play the tour"}>
              {auto ? "❚❚ Pause" : "▶ Play tour"}
            </button>
          </div>
          <p>{cur.does}</p>
          <p className="fd-out"><i>Produces</i> {cur.out}</p>
          <div className="fd-prog" aria-hidden>
            {AGENTS.map((a, i) => (
              <span key={a.id} className={i === sel ? "on" : i < sel ? "past" : ""} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
