import { useEffect, useRef, useState } from "react";
import { api, BusinessContextInput, RunSummary } from "../api";

const EXAMPLES = [
  "Which customer segment drives the most revenue, and is it growing? What should we focus on?",
  "Why is churn happening and which customers are most at risk?",
  "What factors are related to higher salaries, and are there any gaps between groups?",
  "Give me a general understanding of this data and anything unusual in it.",
];
const INDUSTRIES = [
  "E-commerce / Retail",
  "SaaS / Software",
  "Banking / Finance / Insurance",
  "Healthcare",
  "Education",
  "Manufacturing / Operations",
  "HR / People",
  "Marketing / Advertising",
  "Logistics / Supply chain",
  "Hospitality / Travel",
  "Other",
];
const MAX_FILES = 8;
const EMPTY_CTX: BusinessContextInput = { company: "", industry: "", description: "", kpis: "", audience: "", notes: "", currency: "", explore: false };

export default function UploadForm({ onStarted }: { onStarted: (id: string) => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [goal, setGoal] = useState("");
  const [ctx, setCtx] = useState<BusinessContextInput>(EMPTY_CTX);
  const [showCtx, setShowCtx] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const [recent, setRecent] = useState<RunSummary[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.listRuns().then(setRecent).catch(() => undefined);
  }, []);

  const setC = <K extends keyof BusinessContextInput>(k: K, v: BusinessContextInput[K]) => setCtx((c) => ({ ...c, [k]: v }));
  const ctxFilled = ["company", "industry", "description", "kpis", "audience", "notes"].filter((k) => (ctx as unknown as Record<string, string>)[k].trim()).length;

  function addFiles(list: FileList | File[]) {
    const incoming = Array.from(list); // copy now: a FileList is live and is emptied when the input is reset
    setFiles((prev) => {
      const next = [...prev];
      for (const f of incoming) {
        if (!next.some((x) => x.name === f.name && x.size === f.size)) next.push(f);
      }
      return next.slice(0, MAX_FILES);
    });
    setError("");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (files.length === 0) return setError("Choose at least one data file first.");
    if (!ctx.explore && goal.trim().length < 5) return setError("Tell the agent what you want to find out, or tick “I'm not sure what to look for”.");
    setBusy(true);
    setError("");
    try {
      const { run_id } = await api.createRun(files, ctx.explore ? "" : goal.trim(), ctx);
      onStarted(run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
      setBusy(false);
    }
  }

  const totalKb = files.reduce((s, f) => s + f.size, 0) / 1024;

  return (
    <div className="home">
      <section className="hero">
        <h1>Drop in data. Say why. Get the analysis.</h1>
        <p>
          A team of agents profiles your data, connects multiple files on their shared keys, plans only what your goal needs, cleans it, runs the statistics,
          explains what it found, and proposes strategies you can test, plus a Power BI starter pack.
        </p>
      </section>

      <form className="card form" onSubmit={submit}>
        <div
          className={`drop ${drag ? "drop-active" : ""} ${files.length ? "drop-has" : ""}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            addFiles(e.dataTransfer.files);
          }}
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".csv,.tsv,.txt,.xlsx,.xls,.json"
            hidden
            onChange={(e) => {
              if (e.target.files) addFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <strong>{files.length ? "Add more files" : "Drag files here or click to browse"}</strong>
          <span>One or several CSV / Excel / JSON files (an Excel workbook's sheets count as separate tables). Related files are connected automatically.</span>
        </div>

        {files.length > 0 && (
          <ul className="filelist">
            {files.map((f) => (
              <li key={f.name + f.size}>
                <span className="fname">{f.name}</span>
                <span className="muted">{(f.size / 1024).toFixed(0)} KB</span>
                <button type="button" className="x" aria-label={`Remove ${f.name}`} onClick={() => setFiles((p) => p.filter((x) => x !== f))}>
                  ✕
                </button>
              </li>
            ))}
            <li className="filetotal muted">
              {files.length} file{files.length > 1 ? "s" : ""} · {totalKb.toFixed(0)} KB total
              {files.length > 1 && " · they will be linked on matching key columns (e.g. customer_id)"}
            </li>
          </ul>
        )}

        <label htmlFor="goal">What are you trying to find out, and why?</label>
        <textarea
          id="goal"
          rows={3}
          value={goal}
          disabled={ctx.explore}
          onChange={(e) => setGoal(e.target.value)}
          placeholder={ctx.explore ? "Exploratory mode: the agents will decide what matters most." : "e.g. We need to decide which segment to invest in next quarter. Which one drives the most revenue and is it growing?"}
        />
        <label className="check">
          <input type="checkbox" checked={ctx.explore} onChange={(e) => setC("explore", e.target.checked)} />
          <span>I'm not sure what to look for. Explore the data and tell me what matters.</span>
        </label>
        {!ctx.explore && (
          <div className="chips">
            {EXAMPLES.map((ex) => (
              <button type="button" key={ex} className="chip" onClick={() => setGoal(ex)}>
                {ex}
              </button>
            ))}
          </div>
        )}

        <button type="button" className="ctx-toggle" aria-expanded={showCtx} onClick={() => setShowCtx((v) => !v)}>
          <span className="caret">{showCtx ? "▾" : "▸"}</span> Tell us about the business <span className="muted">(optional; better results with it)</span>
          {ctxFilled > 0 && <span className="count">{ctxFilled} filled</span>}
        </button>
        {showCtx && (
          <div className="ctx-grid">
            <div className="field">
              <label htmlFor="company">Company name</label>
              <input id="company" value={ctx.company} maxLength={120} onChange={(e) => setC("company", e.target.value)} placeholder="e.g. Asha Textiles" />
            </div>
            <div className="field">
              <label htmlFor="industry">Industry</label>
              <select id="industry" value={ctx.industry} onChange={(e) => setC("industry", e.target.value)}>
                <option value="">Not sure / skip</option>
                {INDUSTRIES.map((i) => (
                  <option key={i}>{i}</option>
                ))}
              </select>
            </div>
            <div className="field wide">
              <label htmlFor="description">What does the business do? Who are its customers?</label>
              <textarea id="description" rows={2} value={ctx.description} maxLength={1500} onChange={(e) => setC("description", e.target.value)}
                placeholder="e.g. We sell fabrics and ready-made garments to boutiques and direct consumers across India." />
            </div>
            <div className="field wide">
              <label htmlFor="kpis">Which numbers matter most to you? (KPIs, comma-separated)</label>
              <input id="kpis" value={ctx.kpis} onChange={(e) => setC("kpis", e.target.value)} placeholder="e.g. revenue, repeat purchase rate, customer satisfaction" />
            </div>
            <div className="field">
              <label htmlFor="audience">Who will read the results?</label>
              <input id="audience" value={ctx.audience} maxLength={200} onChange={(e) => setC("audience", e.target.value)} placeholder="e.g. Leadership team" />
            </div>
            <div className="field">
              <label htmlFor="currency">Currency (for money columns)</label>
              <select id="currency" value={ctx.currency} onChange={(e) => setC("currency", e.target.value)}>
                <option value="">None</option>
                <option value="₹">₹ Rupee</option>
                <option value="$">$ Dollar</option>
                <option value="€">€ Euro</option>
                <option value="£">£ Pound</option>
              </select>
            </div>
            <div className="field wide">
              <label htmlFor="notes">Anything else the agents should know? (constraints, recent changes, known problems)</label>
              <textarea id="notes" rows={2} value={ctx.notes} maxLength={1500} onChange={(e) => setC("notes", e.target.value)}
                placeholder="e.g. We changed pricing in July. Budget for new initiatives is limited." />
            </div>
            <p className="muted privacy wide">
              What you type here guides the analysis and wording and is never treated as fact about the data. If an AI model is enabled, this text is sent to it, so leave out anything confidential.
            </p>
          </div>
        )}

        {error && <div className="error">{error}</div>}
        <button className="primary" disabled={busy}>
          {busy ? "Starting the agents…" : files.length > 1 ? `Connect ${files.length} files and analyse` : ctx.explore ? "Explore my data" : "Analyse"}
        </button>
      </form>

      {recent.length > 0 && (
        <section className="recent">
          <h3>Recent analyses</h3>
          {recent.map((r) => (
            <a key={r.run_id} className="recent-item" href={`#/run/${r.run_id}`}>
              <span className={`dot dot-${r.status}`} />
              <span className="recent-goal">{r.goal}</span>
              <span className="muted">{r.filename}</span>
            </a>
          ))}
        </section>
      )}
    </div>
  );
}
