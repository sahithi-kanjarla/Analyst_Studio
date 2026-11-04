import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import Dialog from "./Dialog";
import UploadForm from "./UploadForm";

/* The landing page. No screenshots of the sample: the "What you get" cards are small pictures built
   from page elements, with example figures that are labelled as examples. Nothing here names a column
   or a category: the words describe how the app works, not what one file contained. */

/* One icon set: 24px, 1.6 stroke, round caps. */
const Icon = ({ d }: { d: string }) => (
  <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);
const ICONS = {
  upload: "M12 15V4.5M7.5 9 12 4.5 16.5 9M4.5 15v3.5a1 1 0 0 0 1 1h13a1 1 0 0 0 1-1V15",
  link: "M12 4 3.5 8.5 12 13l8.5-4.5L12 4ZM3.5 12.5 12 17l8.5-4.5M3.5 16.5 12 21l8.5-4.5",
  check: "M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17ZM8.3 12.4l2.5 2.5 4.9-5.3",
  report: "M6 3.5h12a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1ZM9 16v-3M12 16V9M15 16v-5",
  play: "M8 5.5v13l11-6.5-11-6.5Z",
  lock: "M7 11V8a5 5 0 0 1 10 0v3M6 11h12a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Z",
  calc: "M5 4.5h14v15H5zM8 8.5h8M8 12.5h2M14 12.5h2M8 16h2M14 16h2",
  source: "M5 5.5h14M5 10h14M5 14.5h8M16 15.5l2 2 3-3.5",
};

const STEPS = [
  { icon: ICONS.upload, title: "Upload files", text: "CSV, Excel or JSON. One file or several that belong together." },
  { icon: ICONS.link, title: "We connect and clean them", text: "Files are linked, columns are understood, and every fix is written down." },
  { icon: ICONS.check, title: "AI plans, code computes", text: "Code calculates and checks every number on all of your rows." },
  { icon: ICONS.report, title: "Report, Dashboard, Insights", text: "The answer, the charts and the findings, ready to read and share." },
];

const TRUST = [
  { icon: ICONS.lock, title: "The AI never receives your rows.", text: "It sees column names, types and summary figures. The values in your file stay in your file." },
  { icon: ICONS.calc, title: "Code computes every figure.", text: "Totals, rankings and charts are calculated by code on your whole cleaned table, then checked." },
  { icon: ICONS.source, title: "Every answer shows its source.", text: "Each finding and each answer says how it was worked out, so you can check it against your own data." },
];

const HELPS = [
  { title: "Hours back", text: "No formulas, pivots or chart-fiddling. Upload the file and read the answer." },
  { title: "A clear next step", text: "Every report ends with what to do, ranked by how much it could help." },
  { title: "Know how far to trust it", text: "Each finding carries a confidence label and the checks behind it, so you act on firm ground." },
];

/** Adds `in` to an element the first time it scrolls into view. Without IntersectionObserver it shows at once. */
function useReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") { setSeen(true); return; }
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { setSeen(true); io.disconnect(); } }, { rootMargin: "0px 0px -12% 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return [ref, seen] as const;
}

/* VIDEO SLOT. To add the demo, put the file at frontend/public/landing/demo.mp4 (and, if you like, a
   still at frontend/public/landing/demo-poster.png). Until the file exists this renders nothing. */
const VIDEO_SRC = "/landing/demo.mp4";
const VIDEO_POSTER = "/landing/demo-poster.png";

function VideoSlot() {
  const [found, setFound] = useState(false);
  const [poster, setPoster] = useState<string | null>(VIDEO_POSTER);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let live = true;
    fetch(VIDEO_SRC, { method: "HEAD" })
      .then((r) => { if (live && r.ok && (r.headers.get("content-type") || "").startsWith("video/")) setFound(true); })
      .catch(() => {});
    return () => { live = false; };
  }, []);
  if (!found) return null;
  return (
    <section className="lp-section lp-video" aria-labelledby="lp-video-title">
      <h2 id="lp-video-title">See it in action</h2>
      <button type="button" className="lp-poster" onClick={() => setOpen(true)} aria-label="Play the demo video">
        {poster && <img src={poster} onError={() => setPoster(null)} alt="" width={1280} height={800} loading="lazy" />}
        <span className="lp-play" aria-hidden="true"><Icon d={ICONS.play} /></span>
      </button>
      {open && (
        <Dialog title="Demo" onClose={() => setOpen(false)} wide>
          <video className="lp-video-el" src={VIDEO_SRC} controls autoPlay playsInline />
        </Dialog>
      )}
    </section>
  );
}

/* Small pictures made of page elements. The figures are examples and say so. */
function Mini({ kind }: { kind: "report" | "dashboard" | "insights" }) {
  if (kind === "report") return (
    <div className="lp-mini" aria-hidden="true">
      <span className="lp-line lp-line-lg" /><span className="lp-line" />
      <div className="lp-tiles">
        <div><b>1,240</b><small>Total</small></div><div><b>+8%</b><small>Change</small></div><div><b>312</b><small>Records</small></div>
      </div>
    </div>
  );
  if (kind === "dashboard") return (
    <div className="lp-mini" aria-hidden="true">
      <div className="lp-chips"><span className="lp-chip lp-chip-on">All</span><span className="lp-chip">Group A</span><span className="lp-chip">Group B</span></div>
      <div className="lp-bars"><i style={{ height: "88%" }} className="hi" /><i style={{ height: "62%" }} /><i style={{ height: "48%" }} /><i style={{ height: "34%" }} /></div>
    </div>
  );
  return (
    <div className="lp-mini" aria-hidden="true">
      <div className="lp-finding"><span className="lp-line lp-line-lg" /><span className="lp-badge">High confidence</span></div>
      <div className="lp-finding"><span className="lp-line" /><span className="lp-badge lp-badge-mid">Medium</span></div>
    </div>
  );
}

function Gets() {
  const items = [
    { kind: "report" as const, title: "Report", text: "The answer in one sentence, the figures behind it, and what to do next." },
    { kind: "dashboard" as const, title: "Dashboard", text: "Click any bar or month to filter every chart at once." },
    { kind: "insights" as const, title: "Insights", text: "Each finding, how far to trust it, and the actions it supports." },
  ];
  return (
    <section className="lp-section" aria-labelledby="lp-gets-title">
      <h2 id="lp-gets-title">What you get</h2>
      <ul className="lp-gets">
        {items.map((it) => (
          <li key={it.title}>
            <Mini kind={it.kind} />
            <h3>{it.title}</h3>
            <p>{it.text}</p>
          </li>
        ))}
      </ul>
      <p className="lp-example">Figures in these pictures are examples.</p>
    </section>
  );
}

function Helps() {
  const [ref, seen] = useReveal<HTMLUListElement>();
  return (
    <section className="lp-section" aria-labelledby="lp-helps-title">
      <h2 id="lp-helps-title">How it helps</h2>
      <ul className={`lp-helps ${seen ? "in" : ""}`} ref={ref}>
        {HELPS.map((h, i) => (
          <li key={h.title} style={{ ["--i" as string]: i }}><h3>{h.title}</h3><p>{h.text}</p></li>
        ))}
      </ul>
    </section>
  );
}

function How() {
  const [ref, seen] = useReveal<HTMLOListElement>();
  return (
    <section className="lp-section" aria-labelledby="lp-how-title">
      <h2 id="lp-how-title">How it works</h2>
      <ol className={`lp-how ${seen ? "in" : ""}`} ref={ref}>
        {STEPS.map((s, i) => (
          <li key={s.title} style={{ ["--i" as string]: i }}>
            <span className="lp-how-ico"><Icon d={s.icon} /></span>
            <h3>{s.title}</h3>
            <p>{s.text}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Trust() {
  const [ref, seen] = useReveal<HTMLUListElement>();
  return (
    <section className="lp-section" aria-labelledby="lp-trust-title">
      <h2 id="lp-trust-title">Why you can trust it</h2>
      <ul className={`lp-trust ${seen ? "in" : ""}`} ref={ref}>
        {TRUST.map((t, i) => (
          <li key={t.title} style={{ ["--i" as string]: i }}>
            <span className="lp-trust-ico"><Icon d={t.icon} /></span>
            <h3>{t.title}</h3>
            <p>{t.text}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function StartPage({ onOpenRun, openNew = false }: { onOpenRun: (id: string) => void; openNew?: boolean }) {
  const [dialog, setDialog] = useState(openNew);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setDialog(openNew); }, [openNew]);
  async function sample() {
    setBusy(true);
    try { onOpenRun((await api.openDemo()).run_id); } catch { setError("The sample could not be opened."); setBusy(false); }
  }
  const buttons = (
    <div className="lp-buttons">
      <button type="button" className="btn-primary lp-btn" onClick={() => setDialog(true)}>Try it</button>
      <button type="button" className="btn-quiet lp-btn lp-btn-quiet" disabled={busy} onClick={sample}>See a sample</button>
    </div>
  );
  return (
    <div className="start lp">
      <header className="lp-hero">
        <div className="lp-hero-text">
          <h1>Know your sales. Trust every number.</h1>
          <p className="lp-sub">Upload your sales files and get a short, checked report that says what to do next.</p>
          {buttons}
          {error && <div className="error" role="alert">{error}</div>}
        </div>
      </header>

      <How />
      <Gets />
      <Helps />
      <Trust />
      <VideoSlot />

      <section className="lp-closing" aria-labelledby="lp-closing-title">
        <h2 id="lp-closing-title">See it on your own numbers.</h2>
        {buttons}
      </section>

      {dialog && (
        <Dialog title="Try it yourself" intro="Add your file and tell us what you want to find out." onClose={() => { setDialog(false); if (window.location.hash === "#/new") window.location.hash = "#/"; }}>
          <UploadForm onStarted={onOpenRun} />
        </Dialog>
      )}
    </div>
  );
}
