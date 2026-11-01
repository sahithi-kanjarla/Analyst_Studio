import { ReactNode, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { NAV_LABEL, NAV_TABS, NavTab } from "../../tabs";
import Menu from "./Menu";

/** The slot in the page where the thin bar of the analysis view goes (App renders it instead of the landing header while a run is open). */
export function useBarSlot(): HTMLElement | null {
  const [slot, setSlot] = useState<HTMLElement | null>(() => document.getElementById("runbar-slot"));
  useEffect(() => { setSlot(document.getElementById("runbar-slot")); }, []);
  return slot;
}

export interface TabInfo { id: NavTab; count?: number; warn?: boolean }

/** One thin bar for the analysis view: the logo (home), the report name, the five tabs, and on the right Export, Review answers and a small menu. */
export default function RunBar({ name, tabs, active, onTab, exportMenu, onReview, onFullScreen, fullScreen, onLeave }: {
  name: string;
  tabs?: TabInfo[];
  active?: NavTab | null;
  onTab?: (t: NavTab) => void;
  exportMenu?: ReactNode;
  onReview?: () => void;
  onFullScreen?: () => void;
  fullScreen?: boolean;
  /** Called before leaving for another page; return false to stay (unsaved answer changes). */
  onLeave?: (href: string) => boolean;
}) {
  const slot = useBarSlot();
  const go = (e: React.MouseEvent, href: string) => {
    if (onLeave && !onLeave(href)) e.preventDefault();
  };
  const bar = (
    <header className="runbar">
      <a className="brand runbar-brand" href="#/" aria-label="Analyst Studio, back to the home page" onClick={(e) => go(e, "#/")}>
        <span className="logo-mark">
          <svg viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><path d="M4 17l5-7 4 4 7-9" /></svg>
        </span>
        <span className="runbar-brand-text">Analyst Studio</span>
      </a>
      <span className="runbar-name" title={name}>{name}</span>
      {tabs && (
        <nav className="runbar-tabs" role="tablist" aria-label="Sections of the report">
          {tabs.map((t) => (
            <button key={t.id} role="tab" type="button" aria-selected={active === t.id} className={`tab ${active === t.id ? "tab-active" : ""}`} onClick={() => onTab?.(t.id)}>
              {NAV_LABEL[t.id]}
              {t.count !== undefined && <span className="count">{t.count}</span>}
              {t.warn && t.count !== undefined && <span className="sr-only"> (warnings)</span>}
            </button>
          ))}
        </nav>
      )}
      <div className="runbar-right">
        {exportMenu}
        {onReview && <button type="button" className="btn-quiet runbar-review" onClick={onReview} title="See, change or re-ask the answers this run used">Review answers</button>}
        <Menu label="More" align="right" className="runbar-more" icon={<span aria-hidden="true">⋯</span>}>
          {(close) => (
            <>
              <a role="menuitem" className="menu-item" href="#/new" onClick={(e) => { close(); go(e, "#/new"); }}>New analysis</a>
              <a role="menuitem" className="menu-item" href="#/glossary" onClick={(e) => { close(); go(e, "#/glossary"); }}>Glossary</a>
              <a role="menuitem" className="menu-item" href="#/history" onClick={(e) => { close(); go(e, "#/history"); }}>History</a>
              {onReview && <button role="menuitem" type="button" className="menu-item only-narrow" onClick={() => { close(); onReview(); }}>Review answers</button>}
              {onFullScreen && <button role="menuitem" type="button" className="menu-item hide-phone" onClick={() => { close(); onFullScreen(); }}>{fullScreen ? "Exit full screen" : "Full screen"}</button>}
            </>
          )}
        </Menu>
      </div>
    </header>
  );
  return slot ? createPortal(bar, slot) : null;
}

export { NAV_TABS };
