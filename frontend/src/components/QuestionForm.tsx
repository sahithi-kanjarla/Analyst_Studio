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

  const [again, setAgain] = useState<string[]>([]);      // saved answers the user wants to be asked about: no option is pre-selected until they pick one
  const reused = set ? set.questions.filter((q) => q.prefill_source === "glossary") : [];
  const pending = set ? set.questions.some((q) => !picked[q.id]) : false;
  const askAgain = (id: string) => {
    setAgain((a) => (a.includes(id) ? a : [...a, id]));
    setPicked((p) => ({ ...p, [id]: "" }));
    document.getElementById(`q-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

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
      {reused.length > 0 && (
        <fieldset className="qitem">
          <legend>Saved answers that will be reused ({reused.length})</legend>
          {reused.map((q) => {
            const label = q.options.find((o) => o.value === q.prefill)?.label ?? "";
            return (
              <div className="qdef" key={q.id}>
                <span>{q.text} <b>{again.includes(q.id) ? "(will be asked again)" : label}</b></span>
                <button type="button" className="chip" onClick={() => document.getElementById(`q-${q.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>Change</button>
                <button type="button" className="chip" onClick={() => askAgain(q.id)}>Ask me again</button>
              </div>
            );
          })}
        </fieldset>
      )}
      {set.questions.map((q, i) => (
        <fieldset key={q.id} id={`q-${q.id}`} className="qitem">
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
                {q.prefill_source === "glossary" && o.value === q.prefill && !again.includes(q.id) && <em className="qtag qtag-g">saved</em>}
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
        <button type="submit" className="primary" disabled={busy || pending} title={pending ? "Pick an answer for the question you asked to be asked again" : ""}>{busy ? "Starting…" : "Continue"}</button>
        <button type="button" className="chip" disabled={busy} onClick={() => send(true)}>Skip, use defaults</button>
      </div>
    </form>
  );
}
