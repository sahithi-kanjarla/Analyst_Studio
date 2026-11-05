import { ReactNode } from "react";

/** One look for the three moments a page has nothing to show yet: loading, empty and error.
 *  Loading says what is being made, empty says what to do next, an error says what went wrong and what to try.
 *  The old class names stay on the element (`error`, `empty-state`, `placeholder`), so anything that finds them still does. */
export default function State({ kind, title, children, action, compact = false }: { kind: "loading" | "empty" | "error"; title?: string; children?: ReactNode; action?: ReactNode; compact?: boolean }) {
  const cls = `state state-${kind} ${compact ? "state-compact" : ""} ${kind === "error" ? "error" : kind === "empty" ? "empty-state" : "placeholder"}`;
  return (
    <div className={cls} role={kind === "error" ? "alert" : kind === "loading" ? "status" : undefined} aria-busy={kind === "loading" ? true : undefined}>
      <span className="state-mark" aria-hidden>
        {kind === "loading" ? <span className="spinner" /> : (
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" focusable="false">
            {kind === "error" ? <path d="M12 8v5M12 16.5h.01M10.3 4.2L2.8 17.5A2 2 0 004.5 20.5h15a2 2 0 001.7-3L13.7 4.2a2 2 0 00-3.4 0z" /> : <path d="M4 7h16M4 12h10M4 17h6" />}
          </svg>
        )}
      </span>
      <div className="state-text">
        {title && <h3>{title}</h3>}
        {kind === "loading" && !children ? null : <div className="state-body">{children}</div>}
        {kind === "loading" && <div className="state-bars" aria-hidden><i /><i /><i /></div>}
        {action && <div className="state-action">{action}</div>}
      </div>
    </div>
  );
}
