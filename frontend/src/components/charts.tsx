import { useEffect, useRef, useState } from "react";

import { ACCENT, BAD, NEUTRAL, NEUTRAL_SOFT, PANEL } from "../palette";

/** One chart style everywhere, from the one shared palette (src/palette.ts, generated from the
 *  chart-tokens block of tokens.css, so these charts and the PNGs the backend draws match exactly).
 *  The rule: the highlighted or selected mark carries the accent, every other mark a muted neutral. */
export { ACCENT, NEUTRAL, NEUTRAL_SOFT };
export const NEG = BAD;

function mix(a: string, b: string, t: number) {
  const n = (h: string, i: number) => parseInt(h.slice(1 + i * 2, 3 + i * 2), 16);
  const c = [0, 1, 2].map((i) => Math.round(n(a, i) * (1 - t) + n(b, i) * t));
  return `#${c.map((x) => x.toString(16).padStart(2, "0")).join("")}`;
}

/** Shade `i` of `n` for a chart whose parts all need their own identity (a share ring, a legend).
 *  One hue, ordered by lightness, so it reads the same with any kind of colour-blindness. */
export function shade(i: number, n: number) {
  return mix(ACCENT, PANEL, n < 2 ? 0 : Math.min(i, n - 1) / (n - 1) * 0.7);
}
/** Kept for older call sites: the first six shades of the accent ramp. */
export const PALETTE = [0, 1, 2, 3, 4, 5].map((i) => shade(i, 6));

export interface Item {
  key: string;
  label: string;
  value: number;
  n?: number;
  other?: boolean; // the pooled 'Other' bar: not selectable
}

interface ChartProps {
  items: Item[];
  selected: string[];
  onToggle: (key: string) => void;
  fmt: (v: number) => string;
}

/** Width of an element, kept up to date, so charts draw at real pixel size instead of scaling their text. */
function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.clientWidth);
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

/** With something selected, the rest of the chart fades right back so the selection is the only thing read. */
const dim = (on: boolean, anySel: boolean) => (anySel && !on ? 0.25 : 1);

/** Horizontal bars, one colour per category (like the reference), click to filter. */
export function HBar({ items, selected, onToggle, fmt }: ChartProps) {
  const [ref, w] = useWidth();
  const row = 30;
  const labelW = Math.min(170, Math.max(104, w * 0.4));
  const valueW = 100;
  const barMax = Math.max(10, w - labelW - valueW - 8);
  const max = Math.max(...items.map((i) => i.value), 0.0001);
  const anySel = selected.length > 0;
  return (
    <div ref={ref} className="chart">
      {w > 0 && (
        <svg width={w} height={items.length * row + 4} role="img">
          {items.map((it, i) => {
            const on = selected.includes(it.key);
            const bw = Math.max(2, (Math.max(it.value, 0) / max) * barMax);
            return (
              <g key={it.key} className="bar-g" transform={`translate(0 ${i * row + 2})`} opacity={dim(on, anySel)} onClick={() => !it.other && onToggle(it.key)}>
                <title>{`${it.label}: ${fmt(it.value)}${it.n !== undefined ? ` (n=${it.n.toLocaleString()})` : ""}`}</title>
                <rect x={0} y={0} width={w} height={row - 4} fill="transparent" />
                <text x={labelW - 8} y={(row - 4) / 2 + 4} textAnchor="end" className="c-label">{clip(it.label, Math.floor(labelW / 6.0))}</text>
                <rect x={labelW} y={3} width={bw} height={row - 10} rx={4} fill={it.other ? NEUTRAL_SOFT : i === 0 ? ACCENT : NEUTRAL} className="bar" />
                <text x={labelW + bw + 6} y={(row - 4) / 2 + 4} className="c-value">{fmt(it.value)}{it.n !== undefined && <tspan className="c-axis" dx={6}>n={it.n.toLocaleString()}</tspan>}</text>
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}

/** Vertical columns over time. Negative values hang below the zero line in orange. */
export function Columns({ items, selected, onToggle, fmt, height = 230 }: ChartProps & { height?: number }) {
  const [ref, w] = useWidth();
  const padL = 44, padR = 6, padT = 10, padB = 26;
  const max = Math.max(...items.map((i) => i.value), 0);
  const min = Math.min(...items.map((i) => i.value), 0);
  const span = max - min || 1;
  const innerW = Math.max(10, w - padL - padR);
  const innerH = height - padT - padB;
  const peak = Math.max(...items.map((i) => i.value));
  const y = (v: number) => padT + ((max - v) / span) * innerH;
  const slot = innerW / Math.max(items.length, 1);
  const bw = Math.max(3, Math.min(46, slot * 0.68));
  const every = Math.max(1, Math.ceil(44 / slot));
  const anySel = selected.length > 0;
  const ticks = [max, (max + min) / 2, min].filter((v, i, a) => a.indexOf(v) === i);
  return (
    <div ref={ref} className="chart">
      {w > 0 && (
        <svg width={w} height={height} role="img">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={padL} x2={w - padR} y1={y(t)} y2={y(t)} className="c-grid" />
              <text x={padL - 6} y={y(t) + 4} textAnchor="end" className="c-axis">{fmt(t)}</text>
            </g>
          ))}
          <line x1={padL} x2={w - padR} y1={y(0)} y2={y(0)} className="c-zero" />
          {items.map((it, i) => {
            const on = selected.includes(it.key);
            const x = padL + i * slot + (slot - bw) / 2;
            const top = Math.min(y(it.value), y(0));
            const h = Math.max(1.5, Math.abs(y(it.value) - y(0)));
            return (
              <g key={it.key} className="bar-g" opacity={dim(on, anySel)} onClick={() => onToggle(it.key)}>
                <title>{`${it.label}: ${fmt(it.value)}`}</title>
                <rect x={padL + i * slot} y={padT} width={slot} height={innerH} fill="transparent" />
                <rect x={x} y={top} width={bw} height={h} rx={3} fill={it.value < 0 ? NEG : it.value === peak ? ACCENT : NEUTRAL} className="bar" />
                {it.value === max && max > 0 && <text x={x + bw / 2} y={top - 4} textAnchor="middle" className="c-value">{fmt(it.value)}</text>}
                {i % every === 0 && <text x={x + bw / 2} y={height - 8} textAnchor="middle" className="c-axis">{it.label}</text>}
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}

/** Donut with its own legend (name, share) so no label ever overlaps. */
export function Donut({ items, selected, onToggle, fmt, maxSize }: ChartProps & { maxSize?: number }) {
  const [ref, w] = useWidth();
  const total = items.reduce((s, i) => s + Math.max(i.value, 0), 0) || 1;
  const size = Math.max(72, Math.min(maxSize ?? 170, 170, Math.max(120, w * 0.4)));      // a tile that is short gets a smaller ring, never a cut-off one
  const r = size / 2 - 6, ir = r * 0.62, c = size / 2;
  const anySel = selected.length > 0;
  let a0 = -Math.PI / 2;
  const arcs = items.map((it, i) => {
    const frac = Math.max(it.value, 0) / total;
    const a1 = a0 + frac * Math.PI * 2 - (frac >= 0.999 ? 0.0001 : 0);
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const p = (a: number, rad: number) => `${c + rad * Math.cos(a)} ${c + rad * Math.sin(a)}`;
    const d = `M ${p(a0, r)} A ${r} ${r} 0 ${large} 1 ${p(a1, r)} L ${p(a1, ir)} A ${ir} ${ir} 0 ${large} 0 ${p(a0, ir)} Z`;
    a0 += frac * Math.PI * 2;
    return { it, d, frac, color: it.other ? NEUTRAL_SOFT : shade(i, items.length) };
  });
  return (
    <div ref={ref} className="chart donut-wrap">
      {w > 0 && (
        <>
          <svg width={size} height={size} role="img">
            {arcs.map(({ it, d, color }) => {
              const on = selected.includes(it.key);
              return (
                <path key={it.key} d={d} fill={color} className="bar" opacity={dim(on, anySel)} onClick={() => onToggle(it.key)}>
                  <title>{`${it.label}: ${fmt(it.value)}`}</title>
                </path>
              );
            })}
            <text x={c} y={c - 2} textAnchor="middle" className="c-center">{fmt(total)}</text>
            <text x={c} y={c + 14} textAnchor="middle" className="c-axis">total</text>
          </svg>
          <ul className="legend">
            {arcs.map(({ it, frac, color }) => {
              const on = selected.includes(it.key);
              return (
                <li key={it.key} style={{ opacity: dim(on, anySel) }} onClick={() => onToggle(it.key)} title={`${it.label}: ${fmt(it.value)}`}>
                  <i style={{ background: color }} />
                  <span className="lg-name">{clip(it.label, 16)}</span>
                  <b>{(frac * 100).toFixed(frac < 0.1 ? 1 : 0)}%</b>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}

function clip(s: string, n: number) {
  return s.length > n ? s.slice(0, Math.max(1, n - 1)) + "…" : s;
}
