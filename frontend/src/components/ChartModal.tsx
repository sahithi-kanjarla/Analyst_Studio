import { ReactNode, useEffect } from "react";
import Dialog from "./Dialog";

export interface ZoomEntry { id: string; node: ReactNode }

/** One chart at full size in a dialog. Esc closes it and focus stays inside (Dialog does both);
 *  the left and right arrows, or the two buttons, move to the neighbouring chart. */
export default function ChartModal({ entries, current, onSelect, onClose }: { entries: ZoomEntry[]; current: string; onSelect: (id: string) => void; onClose: () => void }) {
  const at = Math.max(0, entries.findIndex((e) => e.id === current));
  const go = (d: number) => { if (entries.length > 1) onSelect(entries[(at + d + entries.length) % entries.length].id); };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
      if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });
  if (!entries.length) return null;
  return (
    <Dialog title={entries.length > 1 ? `Chart ${at + 1} of ${entries.length}` : "Chart"} onClose={onClose} wide xClose>
      <div className="zoom-body" key={entries[at].id}>{entries[at].node}</div>
      {entries.length > 1 && (
        <div className="zoom-nav">
          <button className="btn-quiet" onClick={() => go(-1)}>Previous</button>
          <button className="btn-quiet" onClick={() => go(1)}>Next</button>
        </div>
      )}
    </Dialog>
  );
}
