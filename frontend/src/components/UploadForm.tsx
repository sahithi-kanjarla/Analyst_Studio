import { useEffect, useRef, useState } from "react";
import { api, BusinessContextInput, getAccessCode, setAccessCode } from "../api";

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
  const inputRef = useRef<HTMLInputElement>(null);
  const [needCode, setNeedCode] = useState(false);
  const [site, setSite] = useState<{ public_demo?: boolean; retention_days?: number }>({});
  const [code, setCode] = useState(getAccessCode());
  useEffect(() => {
    api.siteConfig().then((h) => { setNeedCode(Boolean(h.requires_code)); setSite(h); }).catch(() => undefined);      // the server asks for a code only when UPLOADS_REQUIRE_CODE is set (or on a public demo)
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
    if (needCode && !code.trim()) return setError("Enter the access code you were given to upload your own data. The sample report needs no code.");
    setBusy(true);
    setError("");
    try {
      setAccessCode(code.trim());
      const { run_id } = await api.createRun(files, ctx.explore ? "" : goal.trim(), ctx);
      onStarted(run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
      setBusy(false);
    }
  }

  const totalKb = files.reduce((s, f) => s + f.size, 0) / 1024;

  return (
      <form className="start-form" onSubmit={submit}>
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
          <span className="up">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 16V4M7 9l5-5 5 5M5 20h14" /></svg>
          </span>
          <strong>{files.length ? "Add more files" : "Drag files here or click to browse"}</strong>
          <span>One or more CSV, Excel or JSON files. If you add several, we link them for you.</span>
          <span className="fmts"><em>.csv</em><em>.xlsx</em><em>.json</em><em>.tsv</em></span>
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
          placeholder={ctx.explore ? "We'll look at the whole file and tell you what matters most." : "e.g. We need to decide which segment to invest in next quarter. Which one drives the most revenue and is it growing?"}
        />
        <label className="check">
          <input type="checkbox" checked={ctx.explore} onChange={(e) => setC("explore", e.target.checked)} />
          <span>I'm not sure what to look for. Explore the data and tell me what matters.</span>
        </label>
        <label className="check">
          <input type="checkbox" checked={!!ctx.review_saved} onChange={(e) => setC("review_saved", e.target.checked)} />
          <span>Show me my saved answers first, so I can change them.</span>
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
              <textarea id="description" rows={2} value={ctx.description} maxLength={500} onChange={(e) => setC("description", e.target.value)}
                placeholder="e.g. We sell fabrics and ready-made garments to boutiques and direct consumers across India." />
            </div>
            <div className="field wide">
              <label htmlFor="kpis">Which numbers matter most to you? (separate with commas)</label>
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
              <label htmlFor="notes">Anything else we should know? (for example, a price change or a known problem)</label>
              <textarea id="notes" rows={2} value={ctx.notes} maxLength={1500} onChange={(e) => setC("notes", e.target.value)}
                placeholder="e.g. We changed pricing in July. Budget for new initiatives is limited." />
            </div>
            <p className="muted privacy wide">
              What you type here guides the analysis and wording and is never treated as fact about the data. If an AI model is enabled, this text is sent to it, so leave out anything confidential.
            </p>
          </div>
        )}

        {needCode && (
          <label className="field access-code">
            <span>Access code</span>
            <input type="password" autoComplete="off" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Needed to upload your own data" />
            <small className="muted">The sample report and demo are open to everyone and need no code.</small>
          </label>
        )}
        <p className="privacy-note muted small" role="note">
          <strong>Privacy.</strong> Your files and the results are stored on this server{site.retention_days ? ` for ${site.retention_days === 1 ? "1 day" : `${site.retention_days} days`}, then deleted automatically` : " until you delete them"}.
          You can delete a run, with its uploaded files, at any time with “Delete my run” in the menu of its report. When an AI model is used, it is sent summaries of your data (column names, counts, ranges and a few common values), never individual rows.
        </p>
        {error && <div className="error">{error}</div>}
        <div className="form-actions">
          <button className="primary" disabled={busy}>
            {busy ? "Starting the agents…" : files.length > 1 ? `Connect ${files.length} files and analyse` : ctx.explore ? "Explore my data" : "Analyse"}
          </button>
        </div>
      </form>
  );
}
