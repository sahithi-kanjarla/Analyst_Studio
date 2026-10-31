import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, LensAsk, LensChip, LensInfo, LensPin, LensTurn } from "../../api";

/** One conversation per run, shared by the side panel, the bottom sheet and the full-width Chat tab. The server keeps the history and the pins, so a reload brings them back. */
export interface LensApi {
  runId: string;
  info: LensInfo | null;
  enabled: boolean;
  ai: boolean;
  turns: LensTurn[];
  pins: LensPin[];
  chips: LensChip[];
  busy: boolean;
  pending: string;
  error: string;
  open: boolean;
  expanded: boolean;
  tabName: string;
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
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [tabName, setTabName] = useState("Report");
  const tab = useRef(tabName);
  tab.current = tabName;

  useEffect(() => {
    let alive = true;
    api.lensInfo(runId).then((i) => {
      if (!alive) return;
      setInfo(i);
      setTurns(i.history);
      setPins(i.pins);
    }).catch(() => alive && setInfo(null));
    return () => { alive = false; };
  }, [runId]);

  const send = useCallback(async (body: LensAsk, label: string) => {
    setBusy(true);
    setPending(label);
    setError("");
    try {
      const t = await api.lensAsk(runId, { ...body, tab: tab.current });
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

  const value = useMemo<LensApi>(() => ({
    runId, info, enabled: !!info?.enabled, ai: !!info?.ai, turns, pins, chips: info?.chips ?? [], busy, pending, error, open, expanded, tabName,
    setOpen, setExpanded, setTabName, send, pin, unpin, pinOf: (turn, target) => pins.find((p) => p.turn_id === turn && p.target === target),
  }), [runId, info, turns, pins, busy, pending, error, open, expanded, tabName, send, pin, unpin]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
