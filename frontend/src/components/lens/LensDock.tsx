import { useEffect } from "react";
import LensChat from "./LensChat";
import { useLens } from "./LensContext";

/** The round Lens button, fixed at the bottom right on every tab, and the panel it opens: docked on the right on a desktop (the report stays readable beside it), a bottom sheet on a phone. */
export default function LensDock() {
  const lens = useLens();
  const shown = !!lens && lens.enabled && lens.open && !lens.expanded;
  useEffect(() => {
    document.body.classList.toggle("lens-docked", shown);
    return () => document.body.classList.remove("lens-docked");
  }, [shown]);
  useEffect(() => {
    if (!shown) return;
    const on = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.querySelector(".dialog-back")) lens!.setOpen(false); };
    document.addEventListener("keydown", on);
    return () => document.removeEventListener("keydown", on);
  }, [shown, lens]);
  if (!lens || !lens.enabled) return null;
  const toggle = () => (lens.expanded ? (lens.setExpanded(false), lens.setOpen(true)) : lens.setOpen(!lens.open));
  return (
    <>
      {shown && (
        <aside className="lens-panel no-print" role="complementary" aria-label="Lens, ask your data">
          <header className="lens-head">
            <div><h2>Lens</h2><p className="muted">Ask your data</p></div>
            <div className="lens-head-actions">
              <button className="btn-quiet" onClick={() => { lens.setExpanded(true); lens.setOpen(false); }} title="Open the same conversation as a full-width tab">Expand</button>
              <button className="btn-quiet" onClick={() => lens.setOpen(false)} aria-label="Close Lens">Close</button>
            </div>
          </header>
          <LensChat variant="panel" />
        </aside>
      )}
      <button className={`lens-fab no-print ${lens.open || lens.expanded ? "lens-fab-on" : ""}`} onClick={toggle} aria-expanded={lens.open || lens.expanded}
        aria-label={lens.expanded ? "Back to the Lens side panel" : lens.open ? "Close Lens" : "Open Lens, ask your data"} title="Lens: ask your data">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6" /><path d="M15 15l5 5" /></svg>
        <span>Lens</span>
      </button>
    </>
  );
}
