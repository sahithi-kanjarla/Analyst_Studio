import { ReactNode, useEffect, useState } from "react";
import { api, AnswerRow, isDemo } from "../api";
import Dialog from "./Dialog";

/** The answers a run used, with where each came from. A changed answer replaces the saved one and the run starts again from the cleaning step;
 *  "Ask me again" forgets the saved answer so the new run asks the question. */
export default function AnswersReview({ runId, onClose, onDirty }: { runId: string; onClose: () => void; onDirty?: (dirty: boolean) => void }) {
  const [rows, setRows] = useState<AnswerRow[] | null>(null);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [again, setAgain] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);

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
  const dirty = changed.length > 0 || again.length > 0;
  useEffect(() => { onDirty?.(dirty); return () => onDirty?.(false); }, [dirty, onDirty]);
  /** Every way of closing (the X, Esc, a click outside, the Close button) comes here: unsaved changes are asked about first. */
  const requestClose = () => { if (dirty && !busy) setConfirming(true); else onClose(); };
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

  const frame = (body: ReactNode) => (
    <Dialog title="Review answers" intro="The answers this run used, and where each came from." onClose={requestClose} xClose>{body}</Dialog>
  );
  if (error && !rows) return frame(<div className="error">{error}</div>);
  if (!rows) return frame(<div className="placeholder"><span className="spinner" /> Loading answers…</div>);
  if (!rows.length) return frame(<p className="muted">This run asked no questions, so there are no answers to review.</p>);

  return frame(
    <div className="qform">
      {confirming && (
        <div className="confirm-bar" role="alertdialog" aria-label="Unsaved changes">
          <span>You have changes that are not saved. Close without re-running?</span>
          <button className="chip" onClick={() => setConfirming(false)}>Keep editing</button>
          <button className="chip chip-danger" onClick={onClose}>Discard and close</button>
        </div>
      )}
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
        {!isDemo(runId) && <button className="primary" disabled={busy || (!changed.length && !again.length)} onClick={rerun}>{busy ? "Starting…" : "Re-run with these answers"}</button>}
        <button className="chip" disabled={busy} onClick={requestClose}>Close</button>
      </div>
    </div>,
  );
}
