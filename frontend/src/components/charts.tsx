import { useEffect, useRef, useState } from "react";

export const PALETTE = ["#3b82f6", "#f4c542", "#ef5b6b", "#22c1b0", "#f08a3c", "#a78bfa", "#7dd3fc", "#94a3b8"];
export const NEG = "#f08a3c";

export interface Item {
  key: string;
  label: string;
  value: number;
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

const dim = (on: boolean, anySel: boolean) => (anySel && !on ? 0.3 : 1);

/** Horizontal bars, one colour per category (like the reference), click to filter. */
export function HBar({ items, selected, onToggle, fmt }: ChartProps) {
  const [ref, w] = useWidth();
  const row = 30;
  const labelW = Math.min(170, Math.max(80, w * 0.36));
  const valueW = 56;
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
              <g key={it.key} className="bar-g" transform={`translate(0 ${i * row + 2})`} opacity={dim(on, anySel)} onClick={() => onToggle(it.key)}>
                <title>{`${it.label}: ${fmt(it.value)}`}</title>
                <rect x={0} y={0} width={w} height={row - 4} fill="transparent" />
                <text x={labelW - 8} y={(row - 4) / 2 + 4} textAnchor="end" className="c-label">{clip(it.label, Math.floor(labelW / 6.6))}</text>
                <rect x={labelW} y={3} width={bw} height={row - 10} rx={4} fill={PALETTE[i % PALETTE.length]} className="bar" />
                <text x={labelW + bw + 6} y={(row - 4) / 2 + 4} className="c-value">{fmt(it.value)}</text>
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
                <rect x={x} y={top} width={bw} height={h} rx={3} fill={it.value < 0 ? NEG : PALETTE[0]} className="bar" />
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
export function Donut({ items, selected, onToggle, fmt }: ChartProps) {
  const [ref, w] = useWidth();
  const total = items.reduce((s, i) => s + Math.max(i.value, 0), 0) || 1;
  const size = Math.min(170, Math.max(120, w * 0.4));
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
    return { it, d, frac, color: PALETTE[i % PALETTE.length] };
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
