import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import Dialog from "./Dialog";
import UploadForm from "./UploadForm";

/* The landing page. Pictures are real screenshots of the Report, Dashboard and Insights tabs in
   frontend/public/landing/ (report.png, dashboard.png, insights.png). Nothing here names a column,
   a category or a figure: the words describe how the app works, not what one file contained. */

const SHOTS = {
  report: { src: "/landing/report.png", alt: "The Report tab: the answer in one sentence, a few key figures, charts and what to do next." },
  dashboard: { src: "/landing/dashboard.png", alt: "The Dashboard tab: filters on top, key figures and charts that respond to a click." },
  insights: { src: "/landing/insights.png", alt: "The Insights tab: each finding with how far to trust it." },
} as const;

const TOUR = [
  { id: "report", label: "Report", line: "The answer in one sentence, the figures behind it, and what to do next.", shot: SHOTS.report },
  { id: "dashboard", label: "Dashboard", line: "Click any bar or month to filter every chart at once.", shot: SHOTS.dashboard },
  { id: "insights", label: "Insights", line: "Each finding, how far to trust it, and the actions it supports.", shot: SHOTS.insights },
] as const;

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

function Frame({ shot, eager = false }: { shot: { src: string; alt: string }; eager?: boolean }) {
  return (
    <figure className="lp-frame">
      <div className="lp-frame-bar" aria-hidden="true"><i /><i /><i /></div>
      <img src={shot.src} alt={shot.alt} width={1280} height={800} loading={eager ? "eager" : "lazy"} decoding="async" />
    </figure>
  );
}

/* VIDEO SLOT. To add the demo, put the file at frontend/public/landing/demo.mp4 (and, if you like, a
   still at frontend/public/landing/demo-poster.png). Until the file exists this renders nothing. */
const VIDEO_SRC = "/landing/demo.mp4";
const VIDEO_POSTER = "/landing/demo-poster.png";

function VideoSlot() {
  const [found, setFound] = useState(false);
  const [poster, setPoster] = useState(VIDEO_POSTER);
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
        <img src={poster} onError={() => setPoster(SHOTS.report.src)} alt="" width={1280} height={800} loading="lazy" />
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

function Tour() {
  const [on, setOn] = useState<number>(0);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  function onKey(e: React.KeyboardEvent, i: number) {
    const move = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!move) return;
    e.preventDefault();
    const next = (i + move + TOUR.length) % TOUR.length;
    setOn(next);
    refs.current[next]?.focus();
  }
  const cur = TOUR[on];
  return (
    <section className="lp-section" aria-labelledby="lp-tour-title">
      <h2 id="lp-tour-title">A look inside</h2>
      <div className="lp-tabs" role="tablist" aria-label="Screens of the app">
        {TOUR.map((t, i) => (
          <button key={t.id} ref={(el) => { refs.current[i] = el; }} type="button" role="tab" id={`lp-tab-${t.id}`} aria-selected={on === i} aria-controls="lp-tour-panel"
            tabIndex={on === i ? 0 : -1} className={`lp-tab ${on === i ? "lp-tab-on" : ""}`} onClick={() => setOn(i)} onKeyDown={(e) => onKey(e, i)}>{t.label}</button>
        ))}
      </div>
      <div id="lp-tour-panel" role="tabpanel" aria-labelledby={`lp-tab-${cur.id}`}>
        <Frame shot={cur.shot} />
        <p className="lp-tour-line">{cur.line}</p>
      </div>
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
        <Frame shot={SHOTS.report} eager />
      </header>

      <How />
      <Trust />
      <Tour />
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
