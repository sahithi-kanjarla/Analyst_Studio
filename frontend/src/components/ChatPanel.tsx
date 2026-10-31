import { useEffect, useRef, useState } from "react";
import { api, AskInfo, ChatAnswer, ChatEntry } from "../api";

function plainTable(rows: (string | number)[][]) {
  return rows.slice(0, 6).map((r) => r.slice(0, 2).map((c) => (typeof c === "number" ? c.toLocaleString(undefined, { maximumFractionDigits: 1 }) : c)).join(": ")).join("\n");
}

function Answer({ entry, runId, onAsk, onPin }: { entry: ChatEntry; runId: string; onAsk: (q: string) => void; onPin: (e: ChatEntry) => void }) {
  const a: ChatAnswer = entry.answer;
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText([a.text, a.rows ? plainTable(a.rows) : ""].filter(Boolean).join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: nothing to do */
    }
  }
  return (
    <div className="msg bot">
      <p>{a.text}</p>
      {a.status === "answered" && a.rows && a.rows.length > 0 && a.op !== "trend" && (
        <table className="table">
          <tbody>
            {a.rows.slice(0, 6).map((r, i) => (
              <tr key={i}>
                <td>{r[0]}</td>
                <td className="num">{typeof r[1] === "number" ? r[1].toLocaleString(undefined, { maximumFractionDigits: 0 }) : r[1]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {a.chart_url && <img className="chat-chart" src={a.chart_url} alt={a.text} loading="lazy" />}
      {a.how && (
        <details>
          <summary>How this was calculated</summary>
          <p><strong>{a.how.operation}.</strong> {a.how.detail}</p>
          <p className="muted">Columns used: {a.how.columns.join(", ")}</p>
        </details>
      )}
      {a.suggestions.length > 0 && (
        <div className="chat-chips">{a.suggestions.map((s) => <button key={s} className="chip" onClick={() => onAsk(s)}>{s}</button>)}</div>
      )}
      {a.status === "answered" && (
        <div className="msg-tools">
          <button className="link" aria-pressed={entry.pinned} onClick={() => onPin(entry)}>{entry.pinned ? "Unpin" : "Pin"}</button>
          <button className="link" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
        </div>
      )}
      <span className="sr-only">{runId}</span>
    </div>
  );
}

/** "Ask about this report": docked on the right on a desktop, a bottom sheet on a phone. Suggested questions run without an AI model. */
export default function ChatPanel({ runId, onClose, embedded = false }: { runId: string; onClose: () => void; embedded?: boolean }) {
  const [info, setInfo] = useState<AskInfo | null>(null);
  const [items, setItems] = useState<ChatEntry[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const list = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!embedded && root.current) window.scrollTo({ top: root.current.getBoundingClientRect().top + window.scrollY - 16 });      // bring the panel, and its input, into view when it opens
  }, [embedded]);
  useEffect(() => {
    api.askInfo(runId).then((i) => { setInfo(i); setItems(i.history); }).catch(() => setError("Questions are not available for this report."));
  }, [runId]);
  useEffect(() => {
    const l = list.current;
    if (!l) return;
    const all = l.querySelectorAll<HTMLElement>("[data-entry]");
    const last = all[all.length - 1];      // show the start of the newest answer; the page itself does not move
    l.scrollTop = busy ? l.scrollHeight : last ? last.offsetTop - 8 : 0;
  }, [items, busy]);

  async function send(body: { question?: string; chip?: string }) {
    setBusy(true);
    setError("");
    try {
      const entry = await api.ask(runId, body);
      setItems((prev) => [...prev, entry]);
      setQ("");
    } catch {
      setError("Sorry, that did not work. Please try again in a moment.");
    } finally {
      setBusy(false);
    }
  }
  async function pin(e: ChatEntry) {
    try {
      const upd = await api.pin(runId, e.id, !e.pinned);
      setItems((prev) => prev.map((x) => (x.id === upd.id ? upd : x)));
    } catch {
      setError("Could not pin that answer.");
    }
  }
  const pinned = items.filter((e) => e.pinned);
  const ask = (text: string) => send({ question: text });

  return (
    <aside ref={root} className={`chat no-print ${embedded ? "chat-embedded" : ""}`} aria-label="Ask about this report">
      <header className="chat-head">
        <h2>Ask about this report</h2>
        <button className="btn-quiet" onClick={onClose} aria-label="Close the chat">Close</button>
      </header>
      <div className="chat-list" aria-live="polite" ref={list}>
        {pinned.length > 0 && <p className="chat-pins">Pinned: {pinned.map((e) => e.question).join(" · ")}</p>}
        {!items.length && !error && (
          <p className="muted">
            Pick a question below{info?.ai ? ", or type your own." : "."} Every figure is worked out by code from your data.
            {info && !info.ai && " Typed questions are answered when they match a suggestion."}
          </p>
        )}
        {items.map((e) => (
          <div key={e.id} data-entry>
            <div className="msg me">{e.question}</div>
            <Answer entry={e} runId={runId} onAsk={ask} onPin={pin} />
          </div>
        ))}
        {busy && <div className="msg bot typing" role="status"><i /><i /><i /><span className="sr-only">Working on it</span></div>}
        {error && <div className="error">{error}</div>}
      </div>
      {info && info.chips.length > 0 && (
        <div className="chat-chips" aria-label="Suggested questions">
          {info.chips.map((c) => <button key={c.id} className="chip" disabled={busy} onClick={() => send({ chip: c.id })}>{c.text}</button>)}
        </div>
      )}
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); if (q.trim() && !busy) ask(q.trim()); }}>
        <label className="sr-only" htmlFor="chat-q">Your question</label>
        <input id="chat-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask a question about your numbers" maxLength={300} />
        <button className="btn-primary" disabled={busy || !q.trim()}>Send</button>
      </form>
    </aside>
  );
}
