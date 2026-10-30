import { useEffect, useState } from "react";
import { api, AnswerRow } from "../api";

/** The answers a run used, with where each came from. A changed answer replaces the saved one and the run starts again from the cleaning step;
 *  "Ask me again" forgets the saved answer so the new run asks the question. */
export default function AnswersReview({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [rows, setRows] = useState<AnswerRow[] | null>(null);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [again, setAgain] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .getAnswersReview(runId)
      .then((r) => {
        setRows(r.answers);
        setPicked(Object.fromEntries(r.answers.map((a) => [a.id, a.answer])));
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load the answers"));
  }, [runId]);

  const changed = rows ? rows.filter((r) => picked[r.id] !== r.answer).map((r) => r.id) : [];
  const toggleAgain = (id: string) => setAgain((a) => (a.includes(id) ? a.filter((x) => x !== id) : [...a, id]));

  const rerun = async () => {
    setBusy(true);
    setError("");
    try {
      const r = await api.rerunWithAnswers(runId, Object.fromEntries(changed.map((id) => [id, picked[id]])), again);
      window.location.hash = `#/run/${r.run_id}`;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not re-run");
      setBusy(false);
    }
  };

  if (error && !rows) return <div className="error">{error}</div>;
  if (!rows) return <div className="card placeholder"><span className="spinner" /> Loading answers…</div>;
  if (!rows.length) return <p className="muted">This run asked no questions, so there are no answers to review.</p>;

  return (
    <div className="qform">
      <p className="muted">Change an answer to re-run from the cleaning step with it. The new answer replaces the saved one. “Ask me again” forgets the saved answer and asks the question on the next run.</p>
      {rows.map((r) => (
        <fieldset key={r.id} className="qitem">
          <legend>{r.text}</legend>
          <p className="muted qwhy">
            {r.reused ? `Reused from your saved answers${r.saved ? ` (saved ${r.saved})` : ""}` : r.source === "user" ? "You answered this" : "Default used"}
          </p>
          <div className="qopts">
            {r.options.map((o) => (
              <label key={o.value} className={`qopt ${picked[r.id] === o.value && !again.includes(r.id) ? "on" : ""}`}>
                <input type="radio" name={r.id} checked={picked[r.id] === o.value} disabled={again.includes(r.id)} onChange={() => setPicked((p) => ({ ...p, [r.id]: o.value }))} />
                <span>{o.label}</span>
                {o.value === r.answer && <em className="qtag qtag-g">used</em>}
              </label>
            ))}
          </div>
          <label className="muted">
            <input type="checkbox" checked={again.includes(r.id)} onChange={() => toggleAgain(r.id)} /> Ask me again
          </label>
        </fieldset>
      ))}
      {error && <div className="error">{error}</div>}
      <div className="qactions">
        <button className="primary" disabled={busy || (!changed.length && !again.length)} onClick={rerun}>{busy ? "Starting…" : "Re-run with these answers"}</button>
        <button className="chip" disabled={busy} onClick={onClose}>Close</button>
      </div>
    </div>
  );
}
