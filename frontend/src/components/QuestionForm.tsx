import { useEffect, useState } from "react";
import { api, QuestionSet } from "../api";

export default function QuestionForm({ runId, onSubmitted }: { runId: string; onSubmitted: () => void }) {
  const [set, setSet] = useState<QuestionSet | null>(null);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState("");
  const [terms, setTerms] = useState<{ term: string; text: string }[]>([{ term: "", text: "" }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .getQuestions(runId)
      .then((s) => {
        setSet(s);
        setPicked(Object.fromEntries(s.questions.map((q) => [q.id, q.prefill ?? q.default])));
        setNotes(s.notes);
        const saved = Object.entries(s.definitions).map(([term, text]) => ({ term, text }));
        setTerms(saved.length ? saved : [{ term: "", text: "" }]);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load the questions"));
  }, [runId]);

  const send = async (skip: boolean) => {
    setBusy(true);
    setError("");
    try {
      const definitions = Object.fromEntries(terms.filter((t) => t.term.trim() && t.text.trim()).map((t) => [t.term.trim(), t.text.trim()]));
      await api.submitAnswers(runId, { answers: skip ? {} : picked, notes: skip ? "" : notes, definitions: skip ? {} : definitions, skip });
      onSubmitted();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not send the answers");
      setBusy(false);
    }
  };

  if (error && !set) return <div className="error">{error}</div>;
  if (!set) return <div className="card placeholder"><span className="spinner" /> Loading questions…</div>;

  return (
    <form className="card qform" onSubmit={(e) => { e.preventDefault(); send(false); }}>
      <h2>A few quick questions</h2>
      <p className="muted">
        These things in your data are ambiguous and would change the numbers. Pick what is right for your business, or skip to use the defaults.
        {set.workspace && set.workspace !== "default" && <> Answers are saved for <b>{set.workspace}</b> and pre-filled next time.</>}
      </p>
      {set.questions.map((q, i) => (
        <fieldset key={q.id} className="qitem">
          <legend>
            <span className="qnum">{i + 1}</span> {q.text}
          </legend>
          <p className="muted qwhy">{q.why}</p>
          <div className="qopts">
            {q.options.map((o) => (
              <label key={o.value} className={`qopt ${picked[q.id] === o.value ? "on" : ""}`}>
                <input type="radio" name={q.id} value={o.value} checked={picked[q.id] === o.value} onChange={() => setPicked((p) => ({ ...p, [q.id]: o.value }))} />
                <span>{o.label}</span>
                {o.value === q.default && <em className="qtag">default</em>}
                {q.prefill_source === "glossary" && o.value === q.prefill && <em className="qtag qtag-g">saved</em>}
              </label>
            ))}
          </div>
        </fieldset>
      ))}

      <fieldset className="qitem">
        <legend>Your own definitions <span className="muted">(optional)</span></legend>
        <p className="muted qwhy">E.g. “active customer: ordered in the last 90 days”. Passed to the AI as unverified context, never treated as fact.</p>
        {terms.map((t, i) => (
          <div className="qdef" key={i}>
            <input placeholder="Term" maxLength={60} value={t.term} onChange={(e) => setTerms((a) => a.map((x, j) => (j === i ? { ...x, term: e.target.value } : x)))} />
            <input placeholder="What it means" maxLength={300} value={t.text} onChange={(e) => setTerms((a) => a.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))} />
          </div>
        ))}
        {terms.length < 20 && <button type="button" className="chip" onClick={() => setTerms((a) => [...a, { term: "", text: "" }])}>+ Add definition</button>}
        <textarea rows={2} maxLength={500} placeholder="Anything else we should know about this data?" value={notes} onChange={(e) => setNotes(e.target.value)} />
      </fieldset>

      {error && <div className="error">{error}</div>}
      <div className="qactions">
        <button type="submit" className="primary" disabled={busy}>{busy ? "Starting…" : "Continue"}</button>
        <button type="button" className="chip" disabled={busy} onClick={() => send(true)}>Skip, use defaults</button>
      </div>
    </form>
  );
}
