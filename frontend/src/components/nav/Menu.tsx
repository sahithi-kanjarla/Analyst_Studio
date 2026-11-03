import { ReactNode, useEffect, useRef, useState } from "react";

/** A small drop-down menu: a button that opens a list of actions. Closes on Escape (focus goes back to the button), on a click outside and after a choice; the arrow keys move between the items. */
export default function Menu({ label, icon, className = "", align = "right", children }: { label: string; icon?: ReactNode; className?: string; align?: "left" | "right"; children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const close = () => setOpen(false);
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const down = (e: MouseEvent) => { if (root.current && !root.current.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); setOpen(false); button.current?.focus(); return; }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const items = Array.from(root.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
      if (!items.length) return;
      e.preventDefault();
      const at = items.indexOf(document.activeElement as HTMLElement);
      items[(at + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
    };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key, true);
    return () => { document.removeEventListener("mousedown", down); document.removeEventListener("keydown", key, true); };
  }, [open]);
  return (
    <div className={`menu ${className}`} ref={root}>
      <button ref={button} type="button" className="btn-quiet menu-button" aria-haspopup="menu" aria-expanded={open} aria-label={icon ? label : undefined} onClick={() => setOpen((o) => !o)}>
        {icon ?? <>{label} <span aria-hidden="true" className="menu-caret">▾</span></>}
      </button>
      {open && <div className={`menu-list menu-${align}`} role="menu" aria-label={label}>{children(close)}</div>}
    </div>
  );
}
