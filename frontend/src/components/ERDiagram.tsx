import { useMemo, useState } from "react";
import { DataModel } from "../api";
import { BAD, CAUTION, GOOD, NEUTRAL } from "../palette";

const W = 230;
const HEAD = 40;
const ROW = 20;
const GAP_X = 120;
const GAP_Y = 50;
const MAX_COLS = 9;

// one shared palette (src/palette.ts): a link that was used, one left out, one refused, one untouched
const STATUS_COLOR: Record<string, string> = { used: GOOD, skipped: CAUTION, rejected: BAD, unused: NEUTRAL };
// What kind of column this is, as a typographic mark rather than a picture
const ROLE_ICON: Record<string, string> = { id: "⌗", numeric: "#", categorical: "Aa", binary: "⊙", date: "▦", text: "¶" };

interface Box {
  name: string;
  x: number;
  y: number;
  h: number;
  cols: { name: string; role: string; key: boolean }[];
  more: number;
  rows: number;
}

/** Entity-relationship diagram drawn as SVG from the data model the Joiner agent produced. */
export default function ERDiagram({ model }: { model: DataModel }) {
  const [hover, setHover] = useState<number | null>(null);

  const { boxes, width, height } = useMemo(() => {
    const keyCols = new Map<string, Set<string>>();
    model.relationships.forEach((r) => {
      for (const [t, c] of [[r.child_table, r.child_col], [r.parent_table, r.parent_col]]) {
        if (!keyCols.has(t)) keyCols.set(t, new Set());
        keyCols.get(t)!.add(c);
      }
    });
    // base table first, then tables with the most links, so the busiest table lands in the middle row
    const degree = (t: string) => model.relationships.filter((r) => r.child_table === t || r.parent_table === t).length;
    const order = [...model.tables].sort((a, b) => (a.name === model.base_table ? -1 : b.name === model.base_table ? 1 : degree(b.name) - degree(a.name)));
    const n = order.length;
    const perRow = n <= 3 ? n : n <= 6 ? 3 : 4;
    if (perRow >= 3) order.splice(Math.floor(perRow / 2), 0, order.shift()!); // busiest table in the middle so links to its neighbours never pass behind another box
    const built: Box[] = order.map((t) => {
      const keys = keyCols.get(t.name) ?? new Set<string>();
      const all = (t.columns ?? []).map((c) => ({ name: c.name, role: c.role, key: keys.has(c.name) }));
      const shown = [...all.filter((c) => c.key), ...all.filter((c) => !c.key)].slice(0, MAX_COLS);
      return { name: t.name, x: 0, y: 0, h: HEAD + shown.length * ROW + (all.length > shown.length ? ROW : 0) + 8, cols: shown, more: Math.max(0, all.length - shown.length), rows: t.rows };
    });
    // place: base table in the first slot of the middle of the first row; others fill around it
    const rowsCount = Math.ceil(n / perRow);
    const rowH = Array.from({ length: rowsCount }, (_, r) => Math.max(...built.slice(r * perRow, r * perRow + perRow).map((b) => b.h)));
    built.forEach((b, i) => {
      const r = Math.floor(i / perRow), c = i % perRow;
      const inRow = Math.min(perRow, n - r * perRow);
      const offset = ((perRow - inRow) * (W + GAP_X)) / 2;
      b.x = 20 + offset + c * (W + GAP_X);
      b.y = 20 + rowH.slice(0, r).reduce((s, h) => s + h + GAP_Y, 0);
    });
    return { boxes: built, width: 40 + perRow * W + (perRow - 1) * GAP_X, height: 40 + rowH.reduce((s, h) => s + h, 0) + (rowsCount - 1) * GAP_Y };
  }, [model]);

  const byName = Object.fromEntries(boxes.map((b) => [b.name, b]));
  const anchorY = (b: Box, col: string) => {
    const i = b.cols.findIndex((c) => c.name === col);
    return b.y + HEAD + (i >= 0 ? i : 0) * ROW + ROW / 2;
  };

  return (
    <div className="er-wrap">
      <svg className="er" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Entity relationship diagram of the uploaded tables" style={{ maxWidth: Math.max(width, 640) }}>
        {model.relationships.map((r, i) => {
          const a = byName[r.child_table], b = byName[r.parent_table];
          if (!a || !b) return null;
          const ya = anchorY(a, r.child_col), yb = anchorY(b, r.parent_col);
          const aRight = a.x + W / 2 < b.x + W / 2;
          const same = Math.abs(a.x - b.x) < 5;
          const xa = same ? a.x + W : aRight ? a.x + W : a.x;
          const xb = same ? b.x + W : aRight ? b.x : b.x + W;
          const dir = same ? 1 : aRight ? 1 : -1;
          const bend = same ? 70 : Math.max(40, Math.abs(xb - xa) / 2);
          const path = `M ${xa} ${ya} C ${xa + dir * bend} ${ya}, ${xb - (same ? -1 : dir) * bend} ${yb}, ${xb} ${yb}`;
          const col = STATUS_COLOR[r.status] ?? STATUS_COLOR.unused;
          const dashed = r.status !== "used";
          const childEnd = r.cardinality === "one-to-one" ? "1" : "N"; // the child side is "many" unless the link is one-to-one; the parent side is always "one"
          return (
            <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} opacity={hover === null || hover === i ? 1 : 0.35}>
              <path d={path} fill="none" stroke="transparent" strokeWidth={14} />
              <path d={path} fill="none" stroke={col} strokeWidth={hover === i ? 3 : 2} strokeDasharray={dashed ? "6 4" : undefined} />
              <text x={xa + dir * 12} y={ya - 5} fontSize={11} fontWeight={700} fill={col} textAnchor="middle">{childEnd}</text>
              <text x={xb - (same ? -1 : dir) * 12} y={yb - 5} fontSize={11} fontWeight={700} fill={col} textAnchor="middle">1</text>
              <title>{`${r.child_table}.${r.child_col} → ${r.parent_table}.${r.parent_col}\n${r.cardinality}, ${(r.child_row_match * 100).toFixed(0)}% of rows match\n${r.status}${r.reason ? ": " + r.reason : ""}`}</title>
            </g>
          );
        })}

        {boxes.map((b) => (
          <g key={b.name} transform={`translate(${b.x},${b.y})`}>
            <rect width={W} height={b.h} rx={10} className="er-box" />
            <path d={`M0 10 a10 10 0 0 1 10 -10 h${W - 20} a10 10 0 0 1 10 10 v${HEAD - 10} h-${W} z`} className={b.name === model.base_table ? "er-head er-head-base" : "er-head"} />
            <text x={14} y={19} className="er-title">{b.name}</text>
            <text x={14} y={34} className="er-sub">{b.rows.toLocaleString()} rows{b.name === model.base_table ? " · analysis grain" : ""}</text>
            {b.cols.map((c, i) => (
              <g key={c.name} transform={`translate(0,${HEAD + i * ROW})`}>
                {c.key && <rect x={4} y={1} width={W - 8} height={ROW - 2} rx={4} className="er-keyrow" />}
                <text x={12} y={14} className="er-icon">{ROLE_ICON[c.role] ?? "•"}</text>
                <text x={36} y={14} className={c.key ? "er-col er-col-key" : "er-col"}>{c.name.length > 24 ? c.name.slice(0, 23) + "…" : c.name}</text>
                <text x={W - 10} y={14} textAnchor="end" className="er-role">{c.role}</text>
              </g>
            ))}
            {b.more > 0 && <text x={14} y={HEAD + b.cols.length * ROW + 14} className="er-role">+ {b.more} more columns</text>}
          </g>
        ))}
      </svg>
      <div className="er-legend">
        <span><i style={{ background: STATUS_COLOR.used }} /> link used to join</span>
        <span><i className="dash" style={{ borderColor: STATUS_COLOR.skipped }} /> found but skipped</span>
        <span><i className="dash" style={{ borderColor: STATUS_COLOR.rejected }} /> rejected as unreliable</span>
        <span className="muted">1 / N = one / many side of the link. Highlighted rows are the key columns. Hover a line for details.</span>
      </div>
    </div>
  );
}
