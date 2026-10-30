import { useEffect, useState } from "react";
import { api, isDemo, RunResult } from "../api";

interface Answer {
  status: "answered" | "no_fit" | "unavailable";
  text: string;
  rows?: (string | number)[][];
  chart_url?: string;
  suggestions: string[];
}

/** 'Ask about this report'. Shown only when the server has CHATBOT=on, and never on the sample run (it makes no AI calls). */
export default function AskReport({ run }: { run: RunResult }) {
  const [enabled, setEnabled] = useState(false);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.health().then((h) => setEnabled(!!h.chatbot)).catch(() => setEnabled(false));
  }, []);
  if (!enabled || isDemo(run.run_id)) return null;

  async function ask(question: string) {
    setQ(question);
    setBusy(true);
    setError("");
    try {
      setAnswer(await api.askReport(run.run_id, question));
    } catch (e) {
      setError(e instanceof Error ? e.message : "The question could not be answered.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="ask no-print" aria-labelledby="ask-title">
      <h3 id="ask-title">Ask about this report</h3>
      <form
        className="ask-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (q.trim()) ask(q.trim());
        }}
      >
        <label className="sr-only" htmlFor="ask-q">Your question</label>
        <input id="ask-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="For example: which store earns the most?" maxLength={300} />
        <button className="primary" disabled={busy || !q.trim()}>{busy ? "Working…" : "Ask"}</button>
      </form>
      {error && <div className="error">{error}</div>}
      {answer && (
        <div className="ask-answer" aria-live="polite">
          <p>{answer.text}</p>
          {answer.rows && answer.rows.length > 0 && (
            <table className="table">
              <tbody>
                {answer.rows.map((r, i) => (
                  <tr key={i}>{r.map((c, j) => <td key={j}>{typeof c === "number" ? c.toLocaleString(undefined, { maximumFractionDigits: 1 }) : c}</td>)}</tr>
                ))}
              </tbody>
            </table>
          )}
          {answer.chart_url && <img src={answer.chart_url} alt={answer.text} className="ask-chart" />}
          {answer.suggestions.length > 0 && (
            <div className="ask-suggest">
              <span className="muted">Questions this report can answer:</span>
              {answer.suggestions.map((s) => (
                <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
