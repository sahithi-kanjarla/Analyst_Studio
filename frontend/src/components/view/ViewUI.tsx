import { Fragment, ReactNode, useState } from "react";
import { useLens } from "../lens/LensContext";
import { useView } from "./ViewContext";

const CHART_TYPES = ["bar", "line", "share", "table"];
const GRAINS = ["day", "week", "month", "quarter", "year"];
const NS = [3, 5, 10];
const TAB_LABEL: Record<string, string> = { report: "Report", insights: "Insights", strategy: "Ideas to try", dashboard: "Dashboard" };

/** The title of an item: the original, or the user's own text with an "Edited by you" label. */
export function ViewTitle({ id, text, tag: Tag = "span" }: { id: string; text: string; tag?: "span" | "h2" | "h3" | "h4" | "h5" }) {
  const v = useView();
  if (!v || !v.data.items[id]) return <Tag>{text}</Tag>;
  return <Tag>{v.title(id, text)}{v.edited(id) && <small className="view-edited"> Edited by you</small>}</Tag>;
}

/** An item's picture: the report's own, or the one Lens redrew when the user changed its type, time grain or top N. */
export function ViewImg({ id, src, alt }: { id: string; src: string; alt: string }) {
  const v = useView();
  const r = v?.data.redrawn[id];
  return (
    <>
      <img src={r?.url ?? src} alt={alt} loading="lazy" />
      {r && <p className="view-note muted small">{r.note}</p>}
    </>
  );
}

function ChangeMenu({ id, onClose }: { id: string; onClose: () => void }) {
  const v = useView();
  const [name, setName] = useState("");
  if (!v) return null;
  const it = v.data.items[id];
  const ch = v.data.resolved.chart[id] ?? {};
  const can = it.changeable ?? [];
  const go = async (...c: Parameters<typeof v.run>) => { if (await v.run(...c)) onClose(); };
  const order = v.order(it.tab);
  const pos = order.indexOf(id);
  return (
    <div className="view-menu" role="menu" aria-label={`Change: ${it.title}`}>
      <div className="view-menu-row">
        <button role="menuitem" className="chip" onClick={() => go({ cmd: v.isPinned(id) ? "unpin" : "pin", id })}>{v.isPinned(id) ? "Unpin" : "Pin to the top"}</button>
        {it.kind === "section" && (
          <>
            <button role="menuitem" className="chip" disabled={pos <= 0} onClick={() => go({ cmd: "move", id, dir: "up" })}>Move up</button>
            <button role="menuitem" className="chip" disabled={pos < 0 || pos >= order.length - 1} onClick={() => go({ cmd: "move", id, dir: "down" })}>Move down</button>
          </>
        )}
      </div>
      {it.kind === "chart" && can.includes("chart") && (
        <div className="view-menu-row" role="group" aria-label="Chart type"><span className="muted small">Show as</span>
          {CHART_TYPES.map((c) => <button key={c} role="menuitemradio" aria-checked={ch.chart === c} className={`chip ${ch.chart === c ? "chip-on" : ""}`} onClick={() => go({ cmd: "chart", id, chart: c })}>{c}</button>)}
        </div>
      )}
      {it.kind === "chart" && can.includes("grain") && (
        <div className="view-menu-row" role="group" aria-label="Time grain"><span className="muted small">Group by</span>
          {GRAINS.map((g) => <button key={g} role="menuitemradio" aria-checked={ch.grain === g} className={`chip ${ch.grain === g ? "chip-on" : ""}`} onClick={() => go({ cmd: "chart", id, grain: g })}>{g}</button>)}
        </div>
      )}
      {it.kind === "chart" && can.includes("n") && (
        <div className="view-menu-row" role="group" aria-label="Top N"><span className="muted small">Top</span>
          {NS.map((n) => <button key={n} role="menuitemradio" aria-checked={ch.n === n} className={`chip ${ch.n === n ? "chip-on" : ""}`} onClick={() => go({ cmd: "chart", id, n })}>{n}</button>)}
        </div>
      )}
      <form className="view-menu-row" onSubmit={(e) => { e.preventDefault(); void go({ cmd: "rename", id, title: name }); }}>
        <label className="sr-only" htmlFor={`rn-${id}`}>New title</label>
        <input id={`rn-${id}`} value={name} maxLength={120} placeholder={v.edited(id) ? v.title(id, it.title) : "Your own title"} onChange={(e) => setName(e.target.value)} />
        <button className="chip" disabled={!name.trim()}>Rename</button>
        {v.edited(id) && <button type="button" className="chip" onClick={() => go({ cmd: "rename", id, title: "" })}>Use original title</button>}
      </form>
    </div>
  );
}

/** The small controls on every card, chart, insight, action and section: Hide, Change and (for cards and charts) Ask Lens about this. */
export function ViewControls({ id, askLens = true }: { id: string; askLens?: boolean }) {
  const v = useView();
  const lens = useLens();
  const [menu, setMenu] = useState(false);
  if (!v || !v.data.items[id]) return null;
  const it = v.data.items[id];
  return (
    <span className="view-controls no-print">
      <button type="button" className="view-btn" onClick={() => v.hide(id)} aria-label={`Hide: ${it.title}`}>Hide</button>
      <button type="button" className="view-btn" aria-expanded={menu} aria-haspopup="menu" onClick={() => setMenu((m) => !m)} aria-label={`Change: ${it.title}`}>Change</button>
      {askLens && lens?.enabled && it.kind !== "section" && (
        <button type="button" className="view-btn view-ask" onClick={() => { lens.setFocus({ id, title: v.title(id, it.title) }); lens.setOpen(true); lens.setExpanded(false); }}>Ask Lens about this</button>
      )}
      {menu && <ChangeMenu id={id} onClose={() => setMenu(false)} />}
    </span>
  );
}

/** Wraps one item. Without view edits (the landing tour, an old run) it renders its children unchanged. A hidden item stays in the page, out of sight, so it can be printed. */
export function ViewItem({ id, children, tag: Tag = "div", className = "", inline = false, askLens = true, controlsFirst = false }: { id: string; children: ReactNode; tag?: "div" | "li" | "section" | "article" | "span"; className?: string; inline?: boolean; askLens?: boolean; controlsFirst?: boolean }) {
  const v = useView();
  if (!v || !v.data.items[id]) return <Tag className={className}>{children}</Tag>;
  if (v.isHidden(id)) return <Tag className={`${className} view-hidden ${v.printSet.has(id) ? "view-print" : ""}`} data-view-id={id}>{children}</Tag>;
  return (
    <Tag className={`${className} view-item ${inline ? "view-inline" : ""} ${v.isPinned(id) ? "is-pinned" : ""}`} data-view-id={id}>
      {v.isPinned(id) && <span className="view-pin no-print">Pinned</span>}
      {controlsFirst && <ViewControls id={id} askLens={askLens} />}
      {children}
      {!controlsFirst && <ViewControls id={id} askLens={askLens} />}
    </Tag>
  );
}

/** The items of a tab split into those shown and those under "Show more" (short view, top N, only the charts that support an action or the summary). */
export function ShowMore({ tab, items }: { tab: string; items: { id: string; node: ReactNode }[] }) {
  const v = useView();
  const shown = items.filter((i) => !v?.isFolded(i.id));
  const rest = items.filter((i) => v?.isFolded(i.id));
  return (
    <>
      {shown.map((i) => <Fragment key={i.id}>{i.node}</Fragment>)}
      {rest.length > 0 && (
        <details className="view-more no-print-fold" data-tab={tab}>
          <summary>Show more ({rest.length})</summary>
          {rest.map((i) => <Fragment key={i.id}>{i.node}</Fragment>)}
        </details>
      )}
    </>
  );
}

/** A tab's sections in the user's order. A hidden section is kept out of sight; a folded one sits under Show more. */
export function Sections({ tab, nodes, selfTitled = [] }: { tab: string; nodes: Record<string, ReactNode>; selfTitled?: string[] }) {
  const v = useView();
  const ids = Object.keys(nodes);
  const order = v ? [...v.order(tab).filter((s) => ids.includes(s)), ...ids.filter((s) => !v.order(tab).includes(s))] : ids;
  return (
    <>
      <ShowMore tab={tab} items={order.map((id) => ({ id, node: <ViewItem id={id} className="view-section" askLens={false} controlsFirst>{!selfTitled.includes(id) && v?.edited(id) && <ViewTitle id={id} tag="h4" text="" />}{nodes[id]}</ViewItem> }))} />
    </>
  );
}

/** The top of a tab: full or short view, Undo, Reset view, and the list of hidden items with a Restore button for each. */
export function TabViewBar({ tab }: { tab: string }) {
  const v = useView();
  if (!v || !v.data.resolved.tabs[tab]) return null;
  const t = v.data.resolved.tabs[tab];
  const hidden = v.data.state.hidden.filter((id) => v.data.items[id]?.tab === tab);        // the ones the user hid, not everything inside a hidden section
  const short = t.short;
  return (
    <div className="view-bar no-print" role="region" aria-label={`Change what ${TAB_LABEL[tab] ?? "this tab"} shows`}>
      <div className="view-bar-row">
        <div role="group" aria-label="View" className="view-seg">
          <button type="button" className={`chip ${!short ? "chip-on" : ""}`} aria-pressed={!short} disabled={v.busy} onClick={() => v.run({ cmd: "set_mode", tab, mode: "full" })}>Full view</button>
          <button type="button" className={`chip ${short ? "chip-on" : ""}`} aria-pressed={short} disabled={v.busy} onClick={() => v.run({ cmd: "set_mode", tab, mode: "short" })}>Short view</button>
        </div>
        {t.top_n != null && <span className="muted small">Top {t.top_n} only</span>}
        {t.only_supporting && <span className="muted small">Only charts that support an action or the summary</span>}
        <span className="view-bar-end">
          {v.data.can_undo && <button type="button" className="btn-quiet" disabled={v.busy} onClick={() => v.undo()}>Undo</button>}
          {!v.data.resolved.original && <button type="button" className="btn-quiet" disabled={v.busy} onClick={() => v.reset()}>Reset view</button>}
        </span>
      </div>
      {hidden.length > 0 && (
        <details className="view-hidden-list">
          <summary>Hidden items ({hidden.length})</summary>
          <ul>
            {hidden.map((id) => (
              <li key={id}>
                <span>{v.title(id, v.data.items[id].title)} <small className="muted">· {v.data.items[id].kind}</small></span>
                <button type="button" className="chip" onClick={() => v.show(id)}>Restore</button>
              </li>
            ))}
          </ul>
          <p className="muted small">Hidden items stay in your saved report. They can be added to a printout from the print panel.</p>
        </details>
      )}
      <p className="muted small view-status" role="status" aria-live="polite">{v.error || v.data.message || ""}</p>
    </div>
  );
}
