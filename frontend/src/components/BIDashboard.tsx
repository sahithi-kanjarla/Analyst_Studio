import { ReactNode, useEffect, useMemo, useState } from "react";
import State from "./State";
import { api, DashData, DashMeasure, DashTile, Finding, RunResult } from "../api";
import { planShare, planTable, plural, SHARE_SLICES, TABLE_ROWS } from "../chartsize";
import { chartTitle, displayName, humanise, plainLabel } from "../display";
import { latestChange } from "../periodchange";
import ChartModal, { ZoomEntry } from "./ChartModal";
import FindingGallery, { FindingZoom, Tile } from "./FindingGallery";
import { useView } from "./view/ViewContext";
import { categoryColours, Columns, Donut, HBar, Item } from "./charts";
import LensPins from "./lens/LensPins";
import { Sections, ShowAllLink, TabViewBar } from "./view/ViewUI";

interface Row {
  d: Record<string, string | null>;
  m: Record<string, number | null>;
  day: string | null;
  ym: string | null;
  y: string | null;
  q: string | null;
}

interface Filters {
  dims: Record<string, string[]>;
  time: string[]; // selected periods (months, or days for very short data)
  quarters: string[];
  years: string[];
}

const EMPTY: Filters = { dims: {}, time: [], quarters: [], years: [] };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const COUNT_MEASURE: DashMeasure = { col: "__count", label: "Records", agg: "sum", fmt: "num", note: "" };

function trim(x: number, digits: number) {
  return x.toFixed(digits).replace(/\.0+$|(\.\d*[1-9])0+$/, "$1");
}
function compact(n: number): string {
  const a = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  const f = (x: number) => trim(x, x >= 100 ? 0 : x >= 10 ? 1 : 2);
  if (a >= 1e9) return sign + f(a / 1e9) + "B";
  if (a >= 1e6) return sign + f(a / 1e6) + "M";
  if (a >= 1e4) return sign + f(a / 1e3) + "K";
  return sign + (a >= 100 ? trim(a, 0) : a >= 10 ? trim(a, 1) : trim(a, 2));
}
const formatter = (m: DashMeasure, cur: string) => (v: number) => (m.fmt === "pct" ? `${trim(v * 100, 1)}%` : m.fmt === "money" ? `${cur}${compact(v)}` : compact(v));

function periodLabel(key: string, grain: "month" | "day", multiYear: boolean) {
  const [y, mo, d] = key.split("-");
  if (grain === "day") return `${Number(d)} ${MONTHS[Number(mo) - 1]}`;
  return multiYear ? `${MONTHS[Number(mo) - 1]} ${y.slice(2)}` : MONTHS[Number(mo) - 1];
}

function Card({ title, sub, wide, kind, action, children }: { title: string; sub?: string; wide?: boolean; kind?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className={`bi-card ${wide ? "bi-wide" : ""}`} data-kind={kind}>
      <h4>{title}{sub && <small>{sub}</small>}</h4>
      {children}
      {action && <p className="bi-card-foot no-print">{action}</p>}
    </section>
  );
}

const BAR_ROW = 30;      // height of one bar row in HBar (charts.tsx)
const TILE_BODY = 230;   // a live chart is drawn at this height (a time series gets more); the page scrolls instead of squeezing a chart to fit
const bodyH = (t: DashTile) => (t.kind === "trend" ? 290 : TILE_BODY);

/** One live chart as a card: a plain title, what it shows, the chart, and a button that opens it at full size. */
function LiveCard({ title, sub, kind, items, node, height, onZoom }: { height: number; title: string; sub: string; kind: string; items?: { measure: string; text: string }[]; node: ReactNode; onZoom: () => void }) {
  return (
    <section className="bi-card bi-tile" data-kind={kind}>
      <h4>{title}{sub && <small>{sub}</small>}</h4>
      {items && items.length > 1 && <ul className="tile-measures small">{items.map((i) => <li key={i.measure + i.text}>{i.text}</li>)}</ul>}
      <div className="bi-tile-body" style={{ minHeight: height }}>{node}</div>
      <p className="bi-card-foot no-print"><button type="button" className="link" onClick={onZoom}>Open full size</button></p>
    </section>
  );
}

/** Power BI-style dashboard: every visual is drawn in the browser from the cleaned data, so selecting a month, segment or year re-filters all the others. */
export default function BIDashboard({ run }: { run: RunResult }) {
  const view = useView();
  const pinKey = (view?.data.state.pinned ?? []).filter((i) => i.startsWith("dashboard:chart:")).join(",");   // a chart pinned (or asked for) in Lens joins the dashboard
  const [data, setData] = useState<DashData | null>(null);
  const [err, setErr] = useState("");
  const [mIdx, setMIdx] = useState(0);
  const [f, setF] = useState<Filters>(EMPTY);
  const [open, setOpen] = useState<string | null>(null);           // the chart shown full size in the dialog: a tile id, or a finding id
  const [allRows, setAllRows] = useState(false);

  useEffect(() => {
    api.getDashboard(run.run_id).then(setData).catch((e) => setErr(e.message));
  }, [run.run_id, pinKey]);

  const plan = data?.plan;
  const tileShown = (t: DashTile) => t.kind !== "finding" || !(view && (view.isHidden(`dashboard:chart:${t.finding_id}`) || view.isFolded(`dashboard:chart:${t.finding_id}`)));
  const shownTiles = (plan?.tiles ?? []).filter(tileShown);

  const rows: Row[] = useMemo(() => {
    if (!data) return [];
    const di = data.columns.indexOf("__date");
    return data.rows.map((r) => {
      const d: Row["d"] = {}, m: Row["m"] = {};
      data.dims.forEach((x) => (d[x.col] = (r[data.columns.indexOf(x.col)] as string | null) ?? null));
      data.measures.forEach((x) => {
        m[x.col] = (r[data.columns.indexOf(x.col)] as number | null) ?? null;
        if (x.den) m[x.den] = (r[data.columns.indexOf(x.den)] as number | null) ?? null;
      });
      const day = di >= 0 ? (r[di] as string | null) : null;
      return { d, m, day, ym: day ? day.slice(0, 7) : null, y: day ? day.slice(0, 4) : null, q: day ? `Q${Math.floor((Number(day.slice(5, 7)) - 1) / 3) + 1}` : null };
    });
  }, [data]);

  const measures = data && data.measures.length ? data.measures : [COUNT_MEASURE];
  const m = measures[Math.min(mIdx, measures.length - 1)];
  const cur = data?.currency ?? "";
  const fmt = formatter(m, cur);
  /** What a column is called on screen: the name the analysis worked out for it, never the name in the file. */
  const label = (col: string) => displayName(col, run.understanding);

  /** One colour per category value, from how many records it has in the whole file: the same value has the same colour in every chart, legend, chip and popup, whatever is filtered. */
  const colourMaps = useMemo(() => {
    const out: Record<string, Record<string, string>> = {};
    (data?.dims ?? []).forEach((d) => {
      const n = new Map<string, number>();
      rows.forEach((r) => { const v = r.d[d.col]; if (v != null) n.set(v, (n.get(v) ?? 0) + 1); });
      out[d.col] = categoryColours([...n.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).map(([k]) => k));
    });
    return out;
  }, [data, rows]);

  const months = useMemo(() => new Set(rows.map((r) => r.ym).filter(Boolean)).size, [rows]);
  const grain: "month" | "day" = months >= 3 ? "month" : "day";
  const years = useMemo(() => [...new Set(rows.map((r) => r.y).filter((y): y is string => !!y))].sort(), [rows]);
  const period = (r: Row) => (grain === "month" ? r.ym : r.day);
  const val = (r: Row, mm: DashMeasure) => (mm.col === "__count" ? 1 : r.m[mm.col] ?? null);

  const passes = (r: Row, exDim?: string, exTime?: boolean) => {
    for (const [col, vals] of Object.entries(f.dims)) if (col !== exDim && vals.length && !vals.includes(r.d[col] ?? "")) return false;
    if (f.years.length && !f.years.includes(r.y ?? "")) return false;
    if (f.quarters.length && !f.quarters.includes(r.q ?? "")) return false;
    if (!exTime && f.time.length && !f.time.includes(period(r) ?? "")) return false;
    return true;
  };

  // a ratio measure weights each row by its denominator, so a group shows total A over total B rather than the mean of row ratios
  const weight = (r: Row, mm: DashMeasure) => (mm.agg === "ratio" && mm.den ? r.m[mm.den] ?? null : 1);
  function group(rs: Row[], key: (r: Row) => string | null, mm: DashMeasure) {
    const g = new Map<string, { s: number; c: number; n: number }>();
    for (const r of rs) {
      const k = key(r), v = val(r, mm), w = weight(r, mm);
      if (k === null || v === null || w === null) continue;
      const e = g.get(k) ?? { s: 0, c: 0, n: 0 };
      e.s += v * w;
      e.c += w;
      e.n += 1;
      g.set(k, e);
    }
    return [...g.entries()].map(([key, e]) => ({ key, value: mm.agg === "sum" ? e.s : e.c ? e.s / e.c : 0, n: e.n, s: e.s, c: e.c }));
  }
  const total = (rs: Row[], mm: DashMeasure) => {
    let s = 0, c = 0;
    for (const r of rs) {
      const v = val(r, mm), w = weight(r, mm);
      if (v !== null && w !== null) { s += v * w; c += w; }
    }
    return mm.agg === "sum" ? s : c ? s / c : 0;
  };

  const filtered = useMemo(() => rows.filter((r) => passes(r)), [rows, f, grain]); // eslint-disable-line react-hooks/exhaustive-deps
  const active = Object.values(f.dims).some((v) => v.length) || f.time.length || f.quarters.length || f.years.length;

  const toggle = (arr: string[], k: string) => (arr.includes(k) ? arr.filter((x) => x !== k) : [...arr, k]);
  const toggleDim = (col: string, k: string) => setF((p) => ({ ...p, dims: { ...p.dims, [col]: toggle(p.dims[col] ?? [], k) } }));

  if (err) return <State kind="error" title="Could not build the dashboard">{err}. Reload the page; the other tabs are not affected.</State>;
  // A large file sends a lot of rows and every figure is worked out in the browser, so hold the page's shape while that happens
  // instead of letting a bare line of text be replaced by a full grid.
  if (!data) {
    return (
      <div className="bi bi-loading" aria-busy="true">
        <p className="bi-muted" role="status">Building your dashboard…</p>
        <div className="bi-kpis">{[0, 1, 2, 3].map((i) => <div className="bi-kpi bi-skel" key={i} aria-hidden />)}</div>
        <div className="bi-grid">{[0, 1, 2].map((i) => <div className={`bi-card bi-skel ${i === 0 ? "bi-wide" : ""}`} key={i} aria-hidden />)}</div>
      </div>
    );
  }

  // ---------------- KPIs
  const overall = total(rows, m);
  const now = total(filtered, m);
  const periods = group(filtered, period, m).sort((a, b) => (a.key < b.key ? -1 : 1));
  const last = periods[periods.length - 1];
  const lead = data.dims[0] ? group(filtered, (r) => r.d[data.dims[0].col], m).sort((a, b) => b.value - a.value)[0] : undefined;
  const kpis: { label: string; value: string; note?: string; tone?: "up" | "down" }[] = [
    { label: plainLabel(m.label), value: fmt(now), note: active && m.agg === "sum" && overall ? `${trim((now / overall) * 100, 0)}% of all` : m.note },
    { label: "Records", value: compact(filtered.length), note: active ? `of ${compact(rows.length)}` : undefined },
  ];
  const other = measures.find((x) => x !== m && x.col !== "__count");
  if (m.agg === "sum" && m.col !== "__count") kpis.push({ label: `Average per record`, value: fmt(filtered.length ? now / filtered.length : 0) });
  else if (other) kpis.push({ label: plainLabel(other.label), value: formatter(other, cur)(total(filtered, other)) });
  if (lead && data.dims[0]) kpis.push({ label: `Top ${label(data.dims[0].col).toLowerCase()}`, value: lead.key, note: m.agg === "sum" && now ? `${trim((lead.value / now) * 100, 0)}% of ${m.label.toLowerCase()}` : fmt(lead.value) });
  const lastDay = filtered.reduce<string | null>((mx, r) => (r.day && (!mx || r.day > mx) ? r.day : mx), null);
  const lc = latestChange(periods.map((p) => ({ key: p.key, value: p.value })), grain, lastDay, m.agg === "sum");
  if (lc) {
    // too few periods, or dates present on under 70% of the days, make the latest-vs-previous change unreliable: say so on the tile
    const partial = group(rows, period, m).length < 6 || (data.date_coverage != null && data.date_coverage < 0.7);
    const cover = lc.days != null && lc.of != null && lc.days < lc.of ? `${lc.days} of ${lc.of} days` : "";
    const when = [periodLabel(last.key, grain, years.length > 1), cover].filter(Boolean).join(" · ");
    if (lc.kind === "too_few") kpis.push({ label: `Latest ${grain} vs previous`, value: "Too early", note: `${when}, too few days to compare` });
    else if (lc.change !== null) {
      const ch = lc.change;
      kpis.push({ label: `Latest ${grain} vs previous${lc.kind === "per_day" ? ", per day" : partial ? " (partial data)" : ""}`, value: `${ch >= 0 ? "+" : ""}${trim(ch * 100, 1)}%`, tone: ch >= 0 ? "up" : "down", note: when });
    }
  }

  // ---------------- visuals
  const trend: Item[] = data.date
    ? group(rows.filter((r) => passes(r, undefined, true)), period, m).sort((a, b) => (a.key < b.key ? -1 : 1)).map((g) => ({ key: g.key, label: periodLabel(g.key, grain, years.length > 1), value: g.value }))
    : [];

  // One shared rule for every chart (chartsize.ts). A share chart only reads well, and is only honest, for a few parts of a total that add up with nothing negative: chartsize decides.
  // Bars that are not drawn here are in the full table under "More charts".
  const measureKind = m.agg === "sum" ? "additive" : "rate";
  const dimValues = (col: string): Item[] =>
    group(rows.filter((r) => passes(r, col)), (r) => r.d[col], m).sort((a, b) => b.value - a.value)
      .map((g) => ({ key: g.key, label: g.key, value: g.value, n: g.n }));
  /** The chart for one live tile, drawn to the height it was given. */
  const liveTile = (t: DashTile, h: number): { title: string; sub: string; node: ReactNode } => {
    if (t.kind === "trend") {
      return { title: chartTitle(m.label, undefined, run.understanding) + ` per ${grain}`,
        sub: trend.length > 1 ? `All ${trend.length} ${plural(grain)}, ${trend[0].label} to ${trend[trend.length - 1].label} · click one to filter` : "",
        node: trend.length > 1 ? <Columns items={trend} selected={f.time} onToggle={(k) => setF((p) => ({ ...p, time: toggle(p.time, k) }))} fmt={fmt} share={m.agg === "sum"} height={Math.max(120, h)} /> : null };
    }
    const col = t.col as string;
    const values = dimValues(col);
    const share = values.length >= 2 && values.length <= SHARE_SLICES ? planShare(values, { measure: measureKind, group: label(col), measureLabel: m.label }) : null;
    if (share?.suitable) {
      return { title: `Share of ${chartTitle(m.label, undefined, run.understanding).toLowerCase()} by ${label(col).toLowerCase()}`, sub: share.subtitle,
        node: <Donut colours={colourMaps[col]} items={share.items as Item[]} selected={f.dims[col] ?? []} onToggle={(k) => toggleDim(col, k)} fmt={fmt} share={m.agg === "sum"} maxSize={h - 8} /> };
    }
    const fits = Math.max(3, Math.floor((h - 4) / BAR_ROW));
    const shown = values.slice(0, fits);
    const what = plural(label(col).toLowerCase());
    return { title: chartTitle(m.label, col, run.understanding),
      sub: values.length > shown.length ? `Top ${shown.length} of ${values.length} ${what} by ${m.label.toLowerCase()}` : `All ${values.length} ${what} by ${m.label.toLowerCase()}`,
      node: <HBar colours={colourMaps[col]} items={shown} selected={f.dims[col] ?? []} onToggle={(k) => toggleDim(col, k)} fmt={fmt} share={m.agg === "sum"} whole={values.reduce((a, v) => a + Math.max(v.value, 0), 0)} /> };
  };
  const findingOf = (t: DashTile) => (run.findings ?? []).find((x) => x.id === t.finding_id);
  const tableDim = data.dims[0];
  const tableRows = tableDim
    ? group(rows.filter((r) => passes(r, tableDim.col)), (r) => r.d[tableDim.col], m).sort((a, b) => b.value - a.value)
    : [];
  const tablePlan = planTable(tableRows, { all: allRows });
  const tableAll = tableRows.length;
  const tableShown = tablePlan.items;
  const tableTotal = tableRows.reduce((s, r) => s + r.value, 0) || 1;
  const tableMax = Math.max(...tableRows.map((r) => Math.abs(r.value)), 0.0001);

  // same order as "Key findings": goal-named first; a small or negligible effect, or a column the goal does not name, is left to "More charts"
  const keyIds = run.report_view?.gallery?.key;
  const withInsight = (run.findings ?? []).filter((x) => x.insight && ["high", "medium"].includes(x.insight.confidence.level));
  const takeaways = (keyIds ? keyIds.map((id) => withInsight.find((x) => x.id === id)).filter((x): x is Finding => !!x) : withInsight.sort((a, b) => b.insight!.score - a.insight!.score)).slice(0, 3);
  const topStrategy = run.strategies?.find((s) => s.kind === "data");
  // the charts the dialog can step through, in the order the page shows them; a finding opened from "What stands out" is added when it is not already on the page
  const allFindings = run.findings ?? [];
  const zoomEntries: ZoomEntry[] = [
    ...shownTiles.flatMap((t): ZoomEntry[] => {
      if (t.kind === "finding") {
        const fd = findingOf(t);
        return fd ? [{ id: t.id, node: <FindingZoom f={fd} index={allFindings.indexOf(fd) + 1} /> }] : [];
      }
      const o = liveTile(t, 460);
      return [{ id: t.id, node: <div className="zoom-live"><h4>{o.title}{o.sub && <small>{o.sub}</small>}</h4>{o.node}</div> }];
    }),
    ...(open && !shownTiles.some((t) => t.id === open) && allFindings.some((x) => x.id === open)
      ? [{ id: open, node: <FindingZoom f={allFindings.find((x) => x.id === open)!} index={allFindings.findIndex((x) => x.id === open) + 1} /> }] : []),
  ];

  const chips: { label: string; drop: () => void; colour?: string }[] = [
    ...Object.entries(f.dims).flatMap(([col, vs]) => vs.map((v) => ({ label: `${label(col)}: ${v}`, drop: () => toggleDim(col, v), colour: colourMaps[col]?.[v] }))),
    ...f.years.map((v) => ({ label: `Year ${v}`, drop: () => setF((p) => ({ ...p, years: toggle(p.years, v) })) })),
    ...f.quarters.map((v) => ({ label: v, drop: () => setF((p) => ({ ...p, quarters: toggle(p.quarters, v) })) })),
    ...f.time.map((v) => ({ label: periodLabel(v, grain, years.length > 1), drop: () => setF((p) => ({ ...p, time: toggle(p.time, v) })) })),
  ];

  const filterDims = (plan?.filters ?? []).map((c) => data.dims.find((d) => d.col === c)).filter((d): d is NonNullable<typeof d> => !!d);
  const moreLive = (plan?.more ?? []).filter((t) => t.kind !== "finding");
  const hiddenTiles = (plan?.tiles ?? []).filter((t) => t.kind === "finding" && view?.isHidden(`dashboard:chart:${t.finding_id}`));

  return (
    <div className="bi">
      <header className="bi-head">
        <div>
          <h3>{data.title ? `${data.title} dashboard` : "Dashboard"}</h3>
          <p className="bi-muted">{data.objective.slice(0, 120)}{data.objective.length > 120 ? "…" : ""}</p>
        </div>
        <div className="bi-slicers">
          {measures.length > 1 && (
            <label className="bi-slicer">
              <span>Measure</span>
              <select value={mIdx} onChange={(e) => setMIdx(Number(e.target.value))}>
                {measures.map((x, i) => <option key={x.col} value={i}>{x.label}</option>)}
              </select>
            </label>
          )}
          {years.length > 1 && (
            <div className="bi-slicer"><span>Year</span>
              <div className="bi-btns">{years.map((y) => <button key={y} className={f.years.includes(y) ? "on" : ""} onClick={() => setF((p) => ({ ...p, years: toggle(p.years, y) }))}>{y}</button>)}</div>
            </div>
          )}
          {data.date && months >= 4 && (
            <div className="bi-slicer"><span>Quarter</span>
              <div className="bi-btns">{["Q1", "Q2", "Q3", "Q4"].map((q) => <button key={q} className={f.quarters.includes(q) ? "on" : ""} onClick={() => setF((p) => ({ ...p, quarters: toggle(p.quarters, q) }))}>{q}</button>)}</div>
            </div>
          )}
          {filterDims.map((d) => {
            const sel = f.dims[d.col] ?? [];
            const opts = [...new Set(rows.map((r) => r.d[d.col]).filter((v): v is string => !!v))].sort();
            return (
              <label className="bi-slicer" key={d.col}>
                <span>{label(d.col)}</span>
                <select value={sel.length === 1 ? sel[0] : sel.length > 1 ? "__multi" : ""} onChange={(e) => setF((p) => ({ ...p, dims: { ...p.dims, [d.col]: e.target.value ? [e.target.value] : [] } }))}>
                  <option value="">All</option>
                  {sel.length > 1 && <option value="__multi" disabled>{sel.length} selected</option>}
                  {opts.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </label>
            );
          })}
        </div>
      </header>

      {active ? (
        <div className="bi-chips">
          <span className="bi-muted"><b>Filtered by</b> · showing {filtered.length.toLocaleString()} of {rows.length.toLocaleString()} records</span>
          {chips.map((c) => <button key={c.label} className="bi-chip" onClick={c.drop} aria-label={`Remove filter ${c.label}`}>{c.colour && <i className="bi-chip-dot" style={{ background: c.colour }} />}{c.label} ✕</button>)}
          <button className="bi-chip bi-clear" onClick={() => setF(EMPTY)}>Clear all</button>
        </div>
      ) : (
        <p className="bi-hint">Tip: click any bar, slice or month to filter every chart. Click again to undo.</p>
      )}

      <div className="bi-kpis">
        {kpis.map((k) => (
          <div className="bi-kpi" key={k.label}>
            <b className={k.tone ? `t-${k.tone}` : ""}>{k.value}</b>
            <span>{k.label}</span>
            {k.note && <em>{k.note}</em>}
          </div>
        ))}
      </div>

      <TabViewBar tab="dashboard" />
      <Sections tab="dashboard" selfTitled={[]} nodes={{
        "dashboard:visuals": (
          <>
      {filtered.length === 0 ? (
        <div className="bi-card bi-empty">No records match these filters. <button className="bi-chip" onClick={() => setF(EMPTY)}>Clear all</button></div>
      ) : (
        <div className="bi-grid bi-flow">
          {shownTiles.map((t) => {
            if (t.kind === "finding") {
              const fd = findingOf(t);
              return fd ? <div key={t.id} className="bi-tile-wrap"><Tile f={fd} items={t.items} und={run.understanding} onOpen={() => setOpen(t.id)} /></div> : null;
            }
            const o = liveTile(t, bodyH(t));
            return <LiveCard key={t.id} kind={t.kind === "trend" ? "series" : "ranked"} items={t.items} title={o.title} sub={o.sub} node={o.node} height={bodyH(t)} onZoom={() => setOpen(t.id)} />;
          })}
        </div>
      )}
      <div className="no-print" hidden>{hiddenTiles.map((t) => { const fd = findingOf(t); return fd ? <Tile key={t.id} f={fd} items={t.items} und={run.understanding} onOpen={() => setOpen(t.id)} /> : null; })}</div>

          </>
        ),
        "dashboard:gallery": (
          <FindingGallery run={run} more={plan?.more ?? []} extraCount={moreLive.filter((t) => liveTile(t, 300).node).length} extra={
            <>
              {moreLive.length > 0 && (
                <div className="bi-grid">
                  {moreLive.map((t) => { const o = liveTile(t, 300); return o.node ? <Card key={t.id} kind="ranked" title={o.title} sub={o.sub}>{o.node}</Card> : null; })}
                </div>
              )}
              {tableDim && tableRows.length > 0 && (
                <Card kind="table" title={`${chartTitle(m.label, tableDim.col, run.understanding)}, in full`}
                  sub={m.agg === "sum" ? `${tablePlan.subtitle} · each one's share of ${m.label.toLowerCase()}` : `${tablePlan.subtitle} · each one against the overall average`} wide>
                  <table className="bi-table">
                    <thead>
                      <tr><th>{label(tableDim.col)}</th><th className="r">Records</th><th className="r">{m.label}</th><th>{m.agg === "sum" ? "Share" : "vs average"}</th></tr>
                    </thead>
                    <tbody>
                      {tableShown.map((r) => {
                        const on = (f.dims[tableDim.col] ?? []).includes(r.key);
                        const diff = overall ? (r.value - overall) / Math.abs(overall) : 0;
                        return (
                          <tr key={r.key} className={on ? "sel" : ""} onClick={() => toggleDim(tableDim.col, r.key)}>
                            <td><i style={{ background: colourMaps[tableDim.col]?.[r.key] }} />{r.key}</td>
                            <td className="r">{r.n.toLocaleString()}</td>
                            <td className="r">{fmt(r.value)}</td>
                            <td>
                              {m.agg === "sum" ? (
                                <span className="share"><s style={{ width: `${Math.max(2, (Math.abs(r.value) / tableMax) * 100)}%` }} />{trim((r.value / tableTotal) * 100, 0)}%</span>
                              ) : (
                                <span className={diff >= 0 ? "t-up" : "t-down"}>{diff >= 0 ? "+" : ""}{trim(diff * 100, 0)}%</span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  {(tablePlan.showAll || allRows) && (
                    <p className="bi-muted small no-print"><button className="bi-chip" aria-expanded={allRows} onClick={() => setAllRows(!allRows)}>{allRows ? `Show top ${TABLE_ROWS}` : `Show all (${tableAll})`}</button></p>
                  )}
                </Card>
              )}
            </>
          } />
        ),
        "dashboard:takeaways": (
          <>

      {(takeaways.length > 0 || topStrategy) && (
        <section className="bi-take">
          <h4>What stands out</h4>
          <ul>
            {takeaways.map((x) => (
              <li key={x.id}><button onClick={() => setOpen(x.id)}>{humanise(x.insight!.headline, run.understanding)}<span className={`bi-tag ${x.insight!.confidence.level}`}>{x.insight!.confidence.level}</span></button></li>
            ))}
            {topStrategy && <li className="bi-reco"><b>Recommended:</b> {humanise(topStrategy.title, run.understanding)}</li>}
          </ul>
        </section>
      )}
          </>
        ),
      }} />
      <ShowAllLink tab="dashboard" />
      <LensPins target="dashboard" />
      {data.sampled && <p className="bi-muted small">Large dataset: the dashboard uses a random sample of {data.rows.length.toLocaleString()} of {data.total_rows.toLocaleString()} rows. Download the full cleaned data from the Data tab.</p>}

      {open && zoomEntries.some((e) => e.id === open) && <ChartModal entries={zoomEntries} current={open} onSelect={setOpen} onClose={() => setOpen(null)} />}
    </div>
  );
}
