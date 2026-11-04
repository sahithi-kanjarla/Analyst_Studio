import ChartFrame from "../ChartFrame";
import { Fragment, ReactNode, useState } from "react";
import { useLens } from "../lens/LensContext";
import { useView } from "./ViewContext";

const TAB_LABEL: Record<string, string> = { report: "Report", insights: "Insights", strategy: "Ideas to try", dashboard: "Dashboard", data: "Your data", model: "How files connect", cleaning: "What we cleaned", plan: "How we analysed", powerbi: "Power BI files", chat: "Lens" };

/** The title of an item: the original, or the user's own text with an "Edited by you" label. */
export function ViewTitle({ id, text, tag: Tag = "span" }: { id: string; text: string; tag?: "span" | "h2" | "h3" | "h4" | "h5" }) {
  const v = useView();
  if (!v || !v.data.items[id]) return <Tag>{text}</Tag>;
  return <Tag>{v.title(id, text)}{v.edited(id) && <small className="view-edited"> Edited by you</small>}</Tag>;
}

/** An item's picture: the report's own, or the one Lens redrew when the user changed its type, time grain or top N (asked for in chat). */
export function ViewImg({ id, src, alt, onOpen }: { id: string; src: string; alt: string; onOpen?: () => void }) {
  const v = useView();
  const r = v?.data.redrawn[id];
  return (
    <>
      <ChartFrame src={r?.url ?? src} alt={alt} onOpen={onOpen} />
      {r && <p className="view-note muted small">{r.note}</p>}
    </>
  );
}

type Tag = "div" | "li" | "section" | "article" | "span";

/** Wraps one item. At rest it shows no controls: clicking it (or pressing Enter on it) selects it and opens Lens with "Focus: <title>"; everything else is asked for in chat.
 *  A hidden item stays in the page, out of sight, so it can be printed. Without view edits (the landing tour, an old run) it renders its children unchanged. */
export function ViewItem({ id, children, tag: Tag = "div", className = "" }: { id: string; children: ReactNode; tag?: Tag; className?: string }) {
  const v = useView();
  const lens = useLens();
  const info = v?.data.items[id];
  if (!v || !info) return <Tag className={className}>{children}</Tag>;
  if (v.isHidden(id)) return <Tag className={`${className} view-hidden ${v.printSet.has(id) ? "view-print" : ""}`} data-view-id={id}>{children}</Tag>;
  const focusable = info.kind !== "section" && !!lens?.enabled;
  const selected = focusable && lens?.focus?.id === id;
  const select = (e: React.SyntheticEvent) => {
    if (!focusable || !lens) return;
    const t = e.target as Element;
    if (e.type === "click" && t.closest("a,button,input,textarea,select,summary,label,[data-nofocus]")) return;
    if (e.type === "click" && (e as React.MouseEvent).defaultPrevented) return;
    const inner = t.closest("[data-focusable]");
    if (inner && inner !== e.currentTarget) return;            // a nested item (a chart inside a card) takes the click
    lens.setFocus({ id, title: v.title(id, info.title) });
    lens.setOpen(true);
    lens.setExpanded(false);
  };
  const props: Record<string, unknown> = focusable
    ? { tabIndex: 0, "data-focusable": "true", "aria-label": undefined, onClick: select, onKeyDown: (e: React.KeyboardEvent) => { if (e.key === "Enter" && e.target === e.currentTarget) { e.preventDefault(); select(e); } } }
    : {};
  const Comp = Tag as React.ElementType;
  return (
    <Comp className={`${className} view-item ${selected ? "is-selected" : ""} ${v.isPinned(id) ? "is-pinned" : ""}`} data-view-id={id} data-selected={selected ? "true" : undefined} {...props}>
      {children}
    </Comp>
  );
}

/** The items of a tab that are shown now. What the short view (or top N, or only the charts that support an action) leaves out is not drawn; the "Show all (n)" link at the end brings it back. */
export function ShowMore({ items }: { tab?: string; items: { id: string; node: ReactNode }[] }) {
  const v = useView();
  return <>{items.filter((i) => !v?.isFolded(i.id)).map((i) => <Fragment key={i.id}>{i.node}</Fragment>)}</>;
}

/** A tab's sections in the user's order. A hidden section is kept out of sight; a folded one is left out. */
export function Sections({ tab, nodes, selfTitled = [] }: { tab: string; nodes: Record<string, ReactNode>; selfTitled?: string[] }) {
  const v = useView();
  const ids = Object.keys(nodes);
  const order = v ? [...v.order(tab).filter((s) => ids.includes(s)), ...ids.filter((s) => !v.order(tab).includes(s))] : ids;
  return (
    <ShowMore tab={tab} items={order.map((id) => ({ id, node: <ViewItem id={id} className="view-section">{!selfTitled.includes(id) && v?.edited(id) && <ViewTitle id={id} tag="h4" text="" />}{nodes[id]}</ViewItem> }))} />
  );
}

/** The one quiet link at the end of a tab: "Show all (n)" when the short view left something out, "Show less" when the tab was switched to the full view. */
export function ShowAllLink({ tab }: { tab: string }) {
  const v = useView();
  const t = v?.data.resolved.tabs[tab];
  if (!v || !t) return null;
  const n = t.folded.length;
  if (n > 0) return <p className="view-all no-print"><button type="button" className="view-all-link" disabled={v.busy} onClick={() => v.run({ cmd: "set_mode", tab, mode: "full" })}>Show all ({n})</button></p>;
  if (!t.short && ["report", "insights", "dashboard"].includes(tab)) return <p className="view-all no-print"><button type="button" className="view-all-link" disabled={v.busy} onClick={() => v.run({ cmd: "set_mode", tab, mode: "short" })}>Show less</button></p>;
  return null;
}

/** One slim bar, shown only when something is hidden or changed ("2 items hidden · Undo · Reset") and gone when the view is back to its default. Hidden items can be restored from its list. */
export function TabViewBar({ tab }: { tab: string }) {
  const v = useView();
  const [open, setOpen] = useState(false);
  if (!v || !v.data.resolved.tabs[tab] || !v.data.bar?.show) return null;
  const hidden = v.data.state.hidden.filter((id) => v.data.items[id]);
  return (
    <div className="view-bar slim no-print" role="region" aria-label={`Changes to ${TAB_LABEL[tab] ?? "this tab"}`}>
      <div className="view-bar-row">
        {hidden.length > 0 ? <button type="button" className="view-bar-text" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{v.data.bar.text}</button> : <span className="view-bar-text">{v.data.bar.text}</span>}
        <span className="view-bar-end">
          {v.data.can_undo && <button type="button" className="view-bar-link" disabled={v.busy} onClick={() => v.undo()}>Undo</button>}
          <button type="button" className="view-bar-link" disabled={v.busy} onClick={() => v.reset()}>Reset</button>
        </span>
      </div>
      {open && hidden.length > 0 && (
        <ul className="view-hidden-list">
          {hidden.map((id) => (
            <li key={id}><span>{v.title(id, v.data.items[id].title)} <small className="muted">· {TAB_LABEL[v.data.items[id].tab] ?? v.data.items[id].tab}</small></span><button type="button" className="view-bar-link" onClick={() => v.show(id)}>Restore</button></li>
          ))}
        </ul>
      )}
      <p className="muted small view-status" role="status" aria-live="polite">{v.error || ""}</p>
    </div>
  );
}
