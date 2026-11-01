import { ReactNode, useEffect, useId, useRef } from "react";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** A centred dialog on a desktop and a full-screen sheet on a phone. Closes on Escape or the Close button; focus stays inside while it is open. */
export default function Dialog({ title, intro, onClose, children, wide = false, xClose = false }: { title: string; intro?: string; onClose: () => void; children: ReactNode; wide?: boolean; xClose?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const titleId = useId();           // one title id per dialog, so stacked dialogs each keep their own name
  const close = useRef(onClose);
  close.current = onClose;           // the latest handler, without re-running the focus set-up on every render of the parent
  useEffect(() => {
    const onClose = () => close.current();
    const before = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    box.current?.querySelector<HTMLElement>("input, button, textarea")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") return onClose();
      if (e.key !== "Tab" || !box.current) return;
      const items = Array.from(box.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      before?.focus();
    };
  }, []);
  return (
    <div className="dialog-back" onMouseDown={(e) => { if (e.target === e.currentTarget) close.current(); }}>
      <div className={`dialog ${wide ? "dialog-wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId} ref={box}>
        <div className="dialog-head">
          <div>
            <h2 id={titleId}>{title}</h2>
            {intro && <p>{intro}</p>}
          </div>
          <button className={xClose ? "btn-quiet dialog-x" : "btn-quiet"} onClick={onClose} aria-label="Close">{xClose ? "✕" : "Close"}</button>
        </div>
        {children}
      </div>
    </div>
  );
}
