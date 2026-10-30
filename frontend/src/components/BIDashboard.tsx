import { useEffect, useMemo, useState } from "react";
import { api, DashData, DashMeasure, RunResult } from "../api";
import FindingGallery from "./FindingGallery";
import InsightCard from "./InsightCard";
import { Columns, Donut, HBar, Item, PALETTE } from "./charts";

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

function Card({ title, sub, wide, children }: { title: string; sub?: string; wide?: boolean; children: React.ReactNode }) {
  return (
    <section className={`bi-card ${wide ? "bi-wide" : ""}`}>
      <h4>{title}{sub && <small>{sub}</small>}</h4>
      {children}
    </section>
  );
}

/** Power BI-style dashboard: every visual is drawn in the browser from the cleaned data, so selecting a month, segment or year re-filters all the others. */
export default function BIDashboard({ run }: { run: RunResult }) {
  const [data, setData] = useState<DashData | null>(null);
  const [err, setErr] = useState("");
  const [mIdx, setMIdx] = useState(0);
  const [f, setF] = useState<Filters>(EMPTY);
  const [open, setOpen] = useState<string | null>(null);
  const [allRows, setAllRows] = useState(false);

  useEffect(() => {
    api.getDashboard(run.run_id).then(setData).catch((e) => setErr(e.message));
  }, [run.run_id]);

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

  if (err) return <div className="error">Could not build the dashboard: {err}</div>;
  if (!data) return <div className="bi"><p className="bi-muted">Building dashboard…</p></div>;

  // ---------------- KPIs
  const overall = total(rows, m);
  const now = total(filtered, m);
  const periods = group(filtered, period, m).sort((a, b) => (a.key < b.key ? -1 : 1));
  const last = periods[periods.length - 1], prev = periods[periods.length - 2];
  const lead = data.dims[0] ? group(filtered, (r) => r.d[data.dims[0].col], m).sort((a, b) => b.value - a.value)[0] : undefined;
  const kpis: { label: string; value: string; note?: string; tone?: "up" | "down" }[] = [
    { label: m.label, value: fmt(now), note: active && m.agg === "sum" && overall ? `${trim((now / overall) * 100, 0)}% of all` : m.note },
    { label: "Records", value: compact(filtered.length), note: active ? `of ${compact(rows.length)}` : undefined },
  ];
  const other = measures.find((x) => x !== m && x.col !== "__count");
  if (m.agg === "sum" && m.col !== "__count") kpis.push({ label: `Average per record`, value: fmt(filtered.length ? now / filtered.length : 0) });
  else if (other) kpis.push({ label: other.label, value: formatter(other, cur)(total(filtered, other)) });
  if (lead && data.dims[0]) kpis.push({ label: `Top ${data.dims[0].col}`, value: lead.key, note: m.agg === "sum" && now ? `${trim((lead.value / now) * 100, 0)}% of ${m.label.toLowerCase()}` : fmt(lead.value) });
  if (last && prev && prev.value) {
    const ch = (last.value - prev.value) / Math.abs(prev.value);
    // too few periods, or dates present on under 70% of the days, make the latest-vs-previous change unreliable: say so on the tile
    const partial = group(rows, period, m).length < 6 || (data.date_coverage != null && data.date_coverage < 0.7);
    kpis.push({ label: `Latest ${grain} vs previous${partial ? " (partial data)" : ""}`, value: `${ch >= 0 ? "+" : ""}${trim(ch * 100, 1)}%`, tone: ch >= 0 ? "up" : "down", note: periodLabel(last.key, grain, years.length > 1) });
  }

  // ---------------- visuals
  const trend: Item[] = data.date
    ? group(rows.filter((r) => passes(r, undefined, true)), period, m).sort((a, b) => (a.key < b.key ? -1 : 1)).map((g) => ({ key: g.key, label: periodLabel(g.key, grain, years.length > 1), value: g.value }))
    : [];

  // A donut only reads well for a few additive, non-negative parts; anything else gets ranked bars.
  const donutDim = m.agg === "sum"
    ? data.dims.find((d) => d.unique >= 2 && d.unique <= 6 && group(rows, (r) => r.d[d.col], m).every((g) => g.value >= 0))
    : undefined;
  const dimItems = (col: string, max: number) => {
    const all = group(rows.filter((r) => passes(r, col)), (r) => r.d[col], m).sort((a, b) => b.value - a.value);
    // top `max` by the plotted value, always keeping the best and the worst group, then one pooled "Other" bar (total, or pooled mean)
    let shown = all.slice(0, max);
    const worst = all[all.length - 1];
    if (worst && !shown.includes(worst)) shown = [...shown.slice(0, max - 1), worst];
    const rest = all.filter((g) => !shown.includes(g));
    const items: Item[] = shown.map((g) => ({ key: g.key, label: g.key, value: g.value, n: g.n }));
    if (rest.length) {
      const n = rest.reduce((s, g) => s + g.n, 0), sum = rest.reduce((s, g) => s + g.s, 0), wsum = rest.reduce((s, g) => s + g.c, 0);
      items.push({ key: "__other", label: `Other (${rest.length} groups)`, value: m.agg === "sum" ? sum : wsum ? sum / wsum : 0, n, other: true });
    }
    return { items, more: rest.length, shown: shown.length, total: all.length };
  };
  const tableDim = data.dims[0];
  const tableRows = tableDim
    ? group(rows.filter((r) => passes(r, tableDim.col)), (r) => r.d[tableDim.col], m).sort((a, b) => b.value - a.value)
    : [];
  const tableAll = tableRows.length;
  const tableShown = allRows ? tableRows : tableRows.slice(0, 10);
  const tableTotal = tableRows.reduce((s, r) => s + r.value, 0) || 1;
  const tableMax = Math.max(...tableRows.map((r) => Math.abs(r.value)), 0.0001);

  const takeaways = (run.findings ?? []).filter((x) => x.insight && ["high", "medium"].includes(x.insight.confidence.level)).sort((a, b) => b.insight!.score - a.insight!.score).slice(0, 3);
  const topStrategy = run.strategies?.find((s) => s.kind === "data");
  const openFinding = (run.findings ?? []).find((x) => x.id === open);

  const chips = [
    ...Object.entries(f.dims).flatMap(([col, vs]) => vs.map((v) => ({ label: `${col}: ${v}`, drop: () => toggleDim(col, v) }))),
    ...f.years.map((v) => ({ label: `Year ${v}`, drop: () => setF((p) => ({ ...p, years: toggle(p.years, v) })) })),
    ...f.quarters.map((v) => ({ label: v, drop: () => setF((p) => ({ ...p, quarters: toggle(p.quarters, v) })) })),
    ...f.time.map((v) => ({ label: periodLabel(v, grain, years.length > 1), drop: () => setF((p) => ({ ...p, time: toggle(p.time, v) })) })),
  ];

  const donutItems = donutDim ? dimItems(donutDim.col, 6).items : [];
  const barDims = data.dims.filter((d) => d !== donutDim);

  return (
    <div className="bi">
      <header className="bi-head">
        <div>
          <h3>{data.title ? `${data.title} dashboard` : "Dashboard"}</h3>
          <p className="bi-muted">{(data.objective || run.goal).slice(0, 120)}{(data.objective || run.goal).length > 120 ? "…" : ""}</p>
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
          {data.dims.slice(0, 3).map((d) => {
            const sel = f.dims[d.col] ?? [];
            const opts = [...new Set(rows.map((r) => r.d[d.col]).filter((v): v is string => !!v))].sort();
            return (
              <label className="bi-slicer" key={d.col}>
                <span>{d.col}</span>
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
          <span className="bi-muted">Showing {filtered.length.toLocaleString()} of {rows.length.toLocaleString()} records</span>
          {chips.map((c) => <button key={c.label} className="bi-chip" onClick={c.drop}>{c.label} ✕</button>)}
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

      {filtered.length === 0 ? (
        <div className="bi-card bi-empty">No records match these filters. <button className="bi-chip" onClick={() => setF(EMPTY)}>Clear all</button></div>
      ) : (
        <div className="bi-grid">
          {trend.length > 1 && (
            <Card title={`${m.label} over time, by ${grain}`} sub={`${trend[0].label} to ${trend[trend.length - 1].label} · click a ${grain} to filter`} wide>
              <Columns items={trend} selected={f.time} onToggle={(k) => setF((p) => ({ ...p, time: toggle(p.time, k) }))} fmt={fmt} />
            </Card>
          )}
          {donutDim && (
            <Card title={`${donutDim.col} share of ${m.label}`} sub={`how ${m.label.toLowerCase()} splits across ${donutDim.unique} ${donutDim.col} values`}>
              <Donut items={donutItems} selected={f.dims[donutDim.col] ?? []} onToggle={(k) => toggleDim(donutDim.col, k)} fmt={fmt} />
            </Card>
          )}
          {barDims.map((d) => {
            const { items, more, shown, total } = dimItems(d.col, d.top ?? 8);
            return (
              <Card key={d.col} title={`${m.label} by ${d.col}`} sub={more ? `showing top ${shown} of ${total} groups; totals and filters use all ${total}` : "highest first"}>
                <HBar items={items} selected={f.dims[d.col] ?? []} onToggle={(k) => toggleDim(d.col, k)} fmt={fmt} />
              </Card>
            );
          })}
          {tableDim && tableRows.length > 0 && (
            <Card title={`${tableDim.col} breakdown`} sub={m.agg === "sum" ? `each ${tableDim.col}'s share of ${m.label.toLowerCase()}` : `each ${tableDim.col} vs the overall average`} wide>
              <table className="bi-table">
                <thead>
                  <tr><th>{tableDim.col}</th><th className="r">Records</th><th className="r">{m.label}</th><th>{m.agg === "sum" ? "Share" : "vs average"}</th></tr>
                </thead>
                <tbody>
                  {tableShown.map((r, i) => {
                    const on = (f.dims[tableDim.col] ?? []).includes(r.key);
                    const diff = overall ? (r.value - overall) / Math.abs(overall) : 0;
                    return (
                      <tr key={r.key} className={on ? "sel" : ""} onClick={() => toggleDim(tableDim.col, r.key)}>
                        <td><i style={{ background: PALETTE[i % PALETTE.length] }} />{r.key}</td>
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
              {tableAll > 10 && (
                <p className="bi-muted small">Showing {tableShown.length} of {tableAll}. <button className="bi-chip" onClick={() => setAllRows(!allRows)}>{allRows ? "Show top 10" : `Show all ${tableAll}`}</button></p>
              )}
            </Card>
          )}
        </div>
      )}

      <FindingGallery run={run} />

      {(takeaways.length > 0 || topStrategy) && (
        <section className="bi-take">
          <h4>What stands out</h4>
          <ul>
            {takeaways.map((x) => (
              <li key={x.id}><button onClick={() => setOpen(x.id)}>{x.insight!.headline}<span className={`bi-tag ${x.insight!.confidence.level}`}>{x.insight!.confidence.level}</span></button></li>
            ))}
            {topStrategy && <li className="bi-reco"><b>Recommended:</b> {topStrategy.title}</li>}
          </ul>
        </section>
      )}
      {data.sampled && <p className="bi-muted small">Large dataset: the dashboard uses a random sample of {data.rows.length.toLocaleString()} of {data.total_rows.toLocaleString()} rows. Download the full cleaned data from the Data tab.</p>}

      {openFinding && (
        <div className="modal" role="dialog" aria-modal="true" aria-label="Insight details" onClick={() => setOpen(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <button className="modal-x" aria-label="Close" onClick={() => setOpen(null)}>✕</button>
            <InsightCard f={openFinding} index={(run.findings ?? []).indexOf(openFinding) + 1} />
          </div>
        </div>
      )}
    </div>
  );
}
