import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, isDemo, LensAsk, LensChip, LensInfo, LensPin, LensTurn, LensView } from "../../api";
import { useView } from "../view/ViewContext";

/** One conversation per run, shared by the side panel, the bottom sheet and the full-width Chat tab. The server keeps the history and the pins, so a reload brings them back. */
export interface LensApi {
  runId: string;
  info: LensInfo | null;
  enabled: boolean;
  ai: boolean;
  turns: LensTurn[];
  pins: LensPin[];
  shortView: LensView | null;
  saveShort: (turn: string) => Promise<void>;
  removeShort: () => Promise<void>;
  chips: LensChip[];
  more: LensChip[];
  busy: boolean;
  pending: string;
  error: string;
  open: boolean;
  expanded: boolean;
  tabName: string;
  /** "Ask Lens about this": the item the next questions are about, until the user removes it. */
  focus: { id: string; title: string } | null;
  setFocus: (f: { id: string; title: string } | null) => void;
  setOpen: (v: boolean) => void;
  setExpanded: (v: boolean) => void;
  setTabName: (v: string) => void;
  send: (body: LensAsk, label: string) => Promise<void>;
  pin: (turn: string, target: "report" | "dashboard") => Promise<void>;
  unpin: (id: string) => Promise<void>;
  pinOf: (turn: string, target: "report" | "dashboard") => LensPin | undefined;
}

const Ctx = createContext<LensApi | null>(null);
export const useLens = () => useContext(Ctx);

export function LensProvider({ runId, children }: { runId: string; children: ReactNode }) {
  const [info, setInfo] = useState<LensInfo | null>(null);
  const [turns, setTurns] = useState<LensTurn[]>([]);
  const [pins, setPins] = useState<LensPin[]>([]);
  const [shortView, setShortView] = useState<LensView | null>(null);
  const [starters, setStarters] = useState<{ chips: LensChip[]; more: LensChip[] }>({ chips: [], more: [] });
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [tabName, setTabName] = useState("Report");
  const [focus, setFocus] = useState<{ id: string; title: string } | null>(null);
  const view = useView();
  const viewRef = useRef(view);
  viewRef.current = view;
  const focusRef = useRef(focus);
  focusRef.current = focus;
  const tab = useRef(tabName);
  tab.current = tabName;

  useEffect(() => {
    // clicking anywhere except a selectable item or Lens itself clears the focus
    const on = (e: MouseEvent) => {
      const t = e.target as Element | null;
      if (!t || !t.closest) return;
      if (t.closest("[data-focusable],.lens-panel,.lens-fab,.lens-chat,.dialog-back,.modal")) return;
      setFocus(null);
    };
    document.addEventListener("click", on);
    return () => document.removeEventListener("click", on);
  }, []);

  useEffect(() => {
    let alive = true;
    api.lensInfo(runId).then((i) => {
      if (!alive) return;
      setInfo(i);
      setTurns(i.history);
      setPins(i.pins);
      setShortView(i.views?.short ?? null);
    }).catch(() => alive && setInfo(null));
    return () => { alive = false; };
  }, [runId]);

  useEffect(() => {
    // the opening suggestions are chosen for the tab that is open: a few, with the rest behind "More ideas"
    let alive = true;
    api.lensInfo(runId, tabName).then((i) => alive && setStarters({ chips: i.chips ?? [], more: i.more ?? [] })).catch(() => undefined);
    return () => { alive = false; };
  }, [runId, tabName]);

  const send = useCallback(async (body: LensAsk, label: string) => {
    setBusy(true);
    setPending(label);
    setError("");
    try {
      const v = viewRef.current;
      const t = await api.lensAsk(runId, { ...body, tab: tab.current, ...(focusRef.current ? { focus: { id: focusRef.current.id } } : {}), ...(isDemo(runId) && v ? { view_state: v.data.state } : {}) });
      if (t.answer.view?.changed) void v?.absorb(t.answer.view.state);          // a chat view change shows up at once, through the same state as the hand controls
      setTurns((prev) => [...prev, t]);
      setInfo((i) => (i && t.answer.source === "ai" ? { ...i, limit: { ...i.limit, asked: i.limit.asked + 1 } } : i));
    } catch (e) {
      setError(e instanceof Error && e.message && e.message.length < 200 ? e.message : "Sorry, that did not work. Please try again in a moment.");
    } finally {
      setBusy(false);
      setPending("");
    }
  }, [runId]);

  const pin = useCallback(async (turn: string, target: "report" | "dashboard") => {
    try {
      const p = await api.lensPin(runId, turn, target);
      setPins((prev) => (prev.some((x) => x.id === p.id) ? prev : [...prev, p]));
    } catch {
      setError("Could not pin that answer.");
    }
  }, [runId]);

  const unpin = useCallback(async (id: string) => {
    try {
      await api.lensUnpin(runId, id);
      setPins((prev) => prev.filter((x) => x.id !== id));
    } catch {
      setError("Could not remove that pin.");
    }
  }, [runId]);

  const saveShort = useCallback(async (turn: string) => {
    try {
      setShortView(await api.lensSaveView(runId, turn));
    } catch {
      setError("Could not keep that short version.");
    }
  }, [runId]);

  const removeShort = useCallback(async () => {
    try {
      await api.lensRemoveView(runId);
      setShortView(null);
    } catch {
      setError("Could not remove the short version.");
    }
  }, [runId]);

  const value = useMemo<LensApi>(() => ({
    runId, info, enabled: !!info?.enabled, ai: !!info?.ai, turns, pins, shortView, saveShort, removeShort, chips: starters.chips, more: starters.more, busy, pending, error, open, expanded, tabName, focus, setFocus,
    setOpen, setExpanded, setTabName, send, pin, unpin, pinOf: (turn, target) => pins.find((p) => p.turn_id === turn && p.target === target),
  }), [runId, info, starters, turns, pins, shortView, saveShort, removeShort, busy, pending, error, open, expanded, tabName, focus, send, pin, unpin]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
