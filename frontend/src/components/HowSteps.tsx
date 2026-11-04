import { ReactNode } from "react";

/** Small line icons for the "How we got this" steps. They carry no meaning on their own: every step also says it in words. */
const PATH: Record<string, string> = {
  file: "M6 3h7l4 4v14H6zM13 3v4h4M9 12h6M9 16h6",
  columns: "M4 5h16v14H4zM9.5 5v14M14.5 5v14",
  broom: "M14 4l6 6M4 20l5-1 9-9-4-4-9 9zM7 17l2 2",
  list: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  check: "M4 12.5l5 5L20 6.5",
  doc: "M5 4h14v16H5zM8.5 9h7M8.5 13h7M8.5 17h4",
  rows: "M4 6h16M4 12h16M4 18h16",
  scale: "M12 4v16M6 20h12M5 8h14M5 8l-2 6a3 3 0 006 0zM19 8l-2 6a3 3 0 006 0z",
};

export function Icon({ name }: { name: string }) {
  return (
    <svg className="hs-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden focusable="false">
      <path d={PATH[name] ?? PATH.check} />
    </svg>
  );
}

export interface Step { icon: string; text: ReactNode }

/** A few steps, one line each, with a small icon: what was done, in plain verbs. The technical detail goes in a separate fold below. */
export default function HowSteps({ steps }: { steps: Step[] }) {
  return (
    <ul className="hs-steps">
      {steps.map((s, i) => <li key={i}><Icon name={s.icon} /><span>{s.text}</span></li>)}
    </ul>
  );
}

/** The one fold that holds everything technical: closed unless the reader asks. */
export function TechDetail({ children, label = "Show technical detail" }: { children: ReactNode; label?: string }) {
  return (
    <details className="hs-tech">
      <summary>{label}</summary>
      <div className="hs-tech-body">{children}</div>
    </details>
  );
}
