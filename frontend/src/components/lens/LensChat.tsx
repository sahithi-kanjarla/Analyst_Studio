import { useEffect, useRef, useState } from "react";
import LensAnswerView from "./LensAnswer";
import { useLens } from "./LensContext";

/** The conversation: history, the input and the suggested questions. Used by the side panel, the bottom sheet and the full-width Chat tab (one shared state). */
const EDIT_CHIPS = ["Make this tab shorter", "Hide the cleaning log", "Show only the top 3 actions"];

export default function LensChat({ variant }: { variant: "panel" | "tab" | "embedded" }) {
  const lens = useLens();
  const [q, setQ] = useState("");
  const [taught, setTaught] = useState(false);          // the editing chips are shown until the first thing is sent in this visit
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const n = lens?.turns.length ?? 0;
  useEffect(() => {
    const l = list.current;
    if (!l) return;
    const all = l.querySelectorAll<HTMLElement>("[data-turn]");
    const last = all[all.length - 1];
    l.scrollTop = lens?.busy ? l.scrollHeight : last ? last.offsetTop - 8 : 0;      // show the start of the newest answer
  }, [n, lens?.busy]);
  useEffect(() => { if (variant !== "embedded") input.current?.focus(); }, [variant]);
  if (!lens || !lens.enabled) return null;
  const lastId = lens.turns[n - 1]?.id;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const t = q.trim();
    if (!t || lens.busy) return;
    setQ("");
    setTaught(true);
    void lens.send({ question: t }, t);
  };
  const limit = lens.info?.limit;
  return (
    <div className={`lens-chat lens-chat-${variant}`}>
      <div className="lens-list" aria-live="polite" ref={list}>
        {n === 0 && (
          <div className="lens-empty">
            <p><strong>Ask about your data.</strong> Every figure is worked out by code from the whole cleaned table.</p>
            <p className="muted">{lens.ai ? "Pick a question or type your own." : "Pick one of the suggested questions. Typed questions are answered when they match one."}</p>
            <div className="lens-chips">{lens.chips.map((c) => <button key={c.id} className="chip" disabled={lens.busy} onClick={() => lens.send({ chip: c.id }, c.text)}>{c.text}</button>)}</div>
          </div>
        )}
        {lens.turns.map((t) => (
          <div key={t.id} data-turn>
            <div className="lens-msg lens-me">{t.question}</div>
            <LensAnswerView turn={t} last={t.id === lastId && !lens.busy} />
          </div>
        ))}
        {lens.busy && (
          <div data-turn>
            {lens.pending && <div className="lens-msg lens-me">{lens.pending}</div>}
            <div className="lens-msg lens-bot typing" role="status"><i /><i /><i /><span className="sr-only">Working on it</span></div>
          </div>
        )}
        {lens.error && <div className="error" role="alert">{lens.error}</div>}
      </div>
      {lens.focus && (
        <div className="lens-focus" role="status">
          <span className="lens-focus-chip">Focus: {lens.focus.title}
            <button type="button" aria-label="Remove focus" onClick={() => lens.setFocus(null)}>✕</button>
          </span>
          <span className="muted lens-small">Questions are answered about this item until you remove it.</span>
        </div>
      )}
      {!taught && variant !== "embedded" && (
        <div className="lens-teach" role="group" aria-label="Change what you see by asking">
          <span className="muted lens-small">Change what you see by asking:</span>
          <div className="lens-chips">{EDIT_CHIPS.map((c) => <button key={c} type="button" className="chip" disabled={lens.busy} onClick={() => { setTaught(true); void lens.send({ question: c }, c); }}>{c}</button>)}</div>
        </div>
      )}
      <form className="lens-input" onSubmit={submit}>
        <label className="sr-only" htmlFor={`lens-q-${variant}`}>Your question</label>
        <input id={`lens-q-${variant}`} ref={input} value={q} onChange={(e) => setQ(e.target.value)} maxLength={300}
          placeholder={lens.ai ? "Ask a question about your data" : "Type a suggested question, or pick one"} />
        <button className="btn-primary" disabled={lens.busy || !q.trim()}>Send</button>
      </form>
      {lens.ai && limit && <p className="muted lens-small lens-limit">{limit.asked} of {limit.max} AI questions used in this run.</p>}
    </div>
  );
}
