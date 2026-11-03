import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, isDemo, ViewCommand, ViewData, ViewState } from "../../api";

/** View edits of one run: which items are hidden, short or full view, order, titles and pins. Hand controls and Lens chat go through the same server code (`/api/runs/{id}/view`), so they give the same state.
 *  The shipped sample run is shared, so its edits are kept in this browser only. Nothing here changes a figure or deletes anything. */
export interface ViewApi {
  data: ViewData;
  busy: boolean;
  error: string;
  run: (...cmds: ViewCommand[]) => Promise<boolean>;
  undo: () => Promise<void>;
  reset: () => Promise<void>;
  hide: (id: string) => Promise<boolean>;
  show: (id: string) => Promise<boolean>;
  absorb: (state?: ViewState | null) => Promise<void>;       // after a chat turn changed the view
  isHidden: (id: string) => boolean;
  isFolded: (id: string) => boolean;
  isPinned: (id: string) => boolean;
  isShort: (tab: string) => boolean;
  /** Every folded section open (the supporting detail on every page); remembered with the rest of this report's view and settable by chat ("show all details"). */
  details: boolean;
  setDetails: (on: boolean) => Promise<boolean>;
  title: (id: string, original: string) => string;
  edited: (id: string) => boolean;
  order: (tab: string) => string[];
  hiddenIn: (tab: string) => string[];
  printSet: Set<string>;
  togglePrint: (id: string) => void;
}

const Ctx = createContext<ViewApi | null>(null);
export const useView = () => useContext(Ctx);
const KEY = (runId: string) => `analyst-studio.view.${runId}`;

function savedState(runId: string): ViewState | undefined {
  try {
    return JSON.parse(window.localStorage.getItem(KEY(runId)) ?? "null") ?? undefined;
  } catch {
    return undefined;
  }
}

export function ViewProvider({ runId, children }: { runId: string; children: ReactNode }) {
  const [data, setData] = useState<ViewData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [printSet, setPrint] = useState<Set<string>>(new Set());
  const local = isDemo(runId);

  const keep = useCallback((d: ViewData) => {
    setData(d);
    if (local) {
      try {
        window.localStorage.setItem(KEY(runId), JSON.stringify(d.state));
      } catch {
        /* storage blocked: the sample's edits last until the page is closed */
      }
    }
  }, [local, runId]);

  useEffect(() => {
    let alive = true;
    const saved = local ? savedState(runId) : undefined;
    (saved ? api.viewApply(runId, { state: saved }) : api.viewGet(runId)).then((d) => alive && setData(d)).catch(() => alive && setData(null));
    return () => { alive = false; };
  }, [runId, local]);

  const run = useCallback(async (...cmds: ViewCommand[]) => {
    if (!data) return false;
    setBusy(true);
    setError("");
    try {
      keep(await api.viewApply(runId, { commands: cmds, ...(local ? { state: data.state } : {}) }));
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "That change could not be made.");
      return false;
    } finally {
      setBusy(false);
    }
  }, [data, keep, local, runId]);

  const absorb = useCallback(async (state?: ViewState | null) => {
    try {
      keep(local && state ? await api.viewApply(runId, { state }) : await api.viewGet(runId));
      setError("");
    } catch {
      /* the next change reloads it */
    }
  }, [keep, local, runId]);

  const value = useMemo<ViewApi | null>(() => {
    if (!data) return null;
    const hidden = new Set(data.resolved.hidden.concat(...Object.values(data.resolved.tabs).map((t) => t.hidden)));
    const folded = new Set(Object.values(data.resolved.tabs).flatMap((t) => t.folded));
    const pinned = new Set(data.resolved.pinned);
    const tabOf = (id: string) => data.items[id]?.tab;
    return {
      data, busy, error, run, absorb, printSet,
      undo: async () => { await run({ cmd: "undo" }); },
      reset: async () => { await run({ cmd: "reset" }); },
      hide: (id) => run({ cmd: "hide", id }),
      show: (id) => run({ cmd: "show", id }),
      isHidden: (id) => hidden.has(id),
      isFolded: (id) => folded.has(id),
      isPinned: (id) => pinned.has(id),
      isShort: (tab) => !!data.resolved.tabs[tab]?.short,
      details: !!data.resolved.details,
      setDetails: (on) => run({ cmd: "set_details", on }),
      title: (id, original) => data.resolved.titles[id] ?? original,
      edited: (id) => id in data.resolved.titles,
      order: (tab) => data.resolved.tabs[tab]?.order ?? [],
      hiddenIn: (tab) => (data.resolved.tabs[tab]?.hidden ?? []).filter((id) => tabOf(id) === tab && data.items[id]?.kind !== undefined),
      togglePrint: (id) => setPrint((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; }),
    };
  }, [data, busy, error, run, absorb, printSet]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
