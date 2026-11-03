/** Which widget an insight reads as, decided from the insight's own metadata (the analysis that produced it, its tables, its sentence) and
 *  never from a column name or example text. Nothing here computes a figure: it picks the figures already in the finding and how to show them. */
import type { CardTable } from "./api";

export type WidgetKind = "ranking" | "share" | "image";
export type Direction = "up" | "down" | null;
export type Theme = "ranking" | "time" | "link" | "shape";

export const THEMES: Record<Theme, { title: string; blurb: string }> = {
  ranking: { title: "Who leads and who lags", blurb: "Groups compared on the same measure." },
  time: { title: "Changes over time", blurb: "Trends and patterns across dates." },
  link: { title: "Things that move together", blurb: "Links between measures. A link is not a cause." },
  shape: { title: "Unusual values and spread", blurb: "How the figures are spread out, and what stands apart." },
};

const RANKING = new Set(["group_compare", "rate_compare", "ranking", "group_table"]);
const TIME = new Set(["trend", "seasonality", "growth"]);
const LINK = new Set(["correlation", "scatter", "chi_square"]);

export const themeOf = (tool: string): Theme => (RANKING.has(tool) ? "ranking" : TIME.has(tool) ? "time" : LINK.has(tool) ? "link" : "shape");

export interface Slice { label: string; value: number; text: string }
export interface Series { title: string; valueHeader: string; items: Slice[]; total: number; share: boolean }

const num = (v: unknown): number | null => {
  const n = parseFloat(String(v ?? "").replace(/[,%\s]/g, ""));
  return Number.isFinite(n) ? n : null;
};

/** A finding's table as labelled values: the first column names each row; the value is the first column of figures that is not a count of rows. */
export function tableSeries(t: CardTable | undefined, tool: string): Series | null {
  if (!t || t.rows.length < 2 || t.columns.length < 2) return null;
  const isShare = (h: string) => /share|%/i.test(h);
  const isCount = (h: string) => /^(rows?|count|n|records?)$/i.test(h.trim());
  const cols = t.columns.map((h, j) => ({ h, j })).filter(({ h, j }) => j > 0 && !isCount(h) && t.rows.every((r) => num(r[j]) !== null));
  const wantShare = tool === "composition";
  const pick = (wantShare ? cols.find((c) => isShare(c.h)) : undefined) ?? cols.find((c) => !isShare(c.h)) ?? cols[0];
  if (!pick) return null;
  return {
    title: t.title.replace(/\s*\((?:table|sum|avg|average|mean|count)\)\s*$/i, "").replace(/^\s*(total|sum of)\s+/i, ""),
    valueHeader: pick.h,
    share: isShare(pick.h),
    total: t.total,
    items: t.rows.map((r) => ({ label: String(r[0]), value: num(r[pick.j]) as number, text: String(r[pick.j]) })),
  };
}

export function widgetKind(tool: string, tables: CardTable[], chartUrl?: string): WidgetKind {
  const s = tableSeries(tables[0], tool);
  if (s && RANKING.has(tool)) return "ranking";
  if (s && tool === "composition" && s.share) return "share";
  return chartUrl ? "image" : "ranking";
}

/** The first figure a sentence puts forward: a multiple, a percentage or a correlation. Taken from the sentence as written. */
export function bigNumber(headline: string, tool: string): { value: string; label: string; sign: 1 | -1 | 0 } | null {
  const h = String(headline ?? "");
  const rho = h.match(/\b(?:rho|r)\s*=\s*([+\-−]?\d*\.\d+)/i);
  if (rho) return { value: rho[1].replace("−", "-"), label: "Strength of the link (−1 to +1)", sign: rho[1].startsWith("-") || rho[1].startsWith("−") ? -1 : 1 };
  const mult = h.match(/(\d[\d,]*(?:\.\d+)?)\s?(?:×|x\b)/);
  if (mult) return { value: `${mult[1]}×`, label: "Top compared with the lowest", sign: 0 };
  const pct = h.match(/([+\-−]?\d[\d,]*(?:\.\d+)?)\s?%/);
  if (pct) {
    const v = pct[1].replace("−", "-");
    return { value: `${v}%`, label: TIME.has(tool) ? "Change over the period" : "Figure in this finding", sign: v.startsWith("-") ? -1 : v.startsWith("+") ? 1 : 0 };
  }
  return null;
}

/** Up or down, only where the sentence itself says so and the finding is about change or a link. */
export function direction(headline: string, tool: string, sign: 1 | -1 | 0 = 0): Direction {
  if (!TIME.has(tool) && !LINK.has(tool)) return null;
  if (sign) return sign > 0 ? "up" : "down";
  const up = /\b(rises?|rising|up|upward|increas\w*|grow\w*|higher)\b/i.test(headline);
  const down = /\b(falls?|falling|down|downward|declin\w*|decreas\w*|drop\w*|lower)\b/i.test(headline);
  return up !== down ? (up ? "up" : "down") : null;
}

const plain = (t: string) => String(t ?? "").replace(/[*_`#]/g, "").replace(/\s+/g, " ").trim();

/** One or two sentences, short. */
export function takeaway(why: string, observation: string, max = 230): string {
  const src = plain(why) || plain(observation);
  const parts = src.match(/.+?[.!?]+(?=\s|$)\s*|.+$/g) ?? [src];       // a full stop ends a sentence only when a space or the end follows ("60.0" stays whole)
  let out = "";
  for (const p of parts.slice(0, 2)) {
    if ((out + p).length > max && out) break;
    out += p;
  }
  return out.trim();
}

/** Slices for a share bar: the biggest few, the rest pooled as "Other" (only from the figures the finding already lists). */
export function shareSlices(items: Slice[], keep = 5): Slice[] {
  if (items.length <= keep + 1) return items;
  const rest = items.slice(keep);
  const v = rest.reduce((s, x) => s + x.value, 0);
  return [...items.slice(0, keep), { label: `Other (${rest.length} items)`, value: v, text: `${Math.round(v * 10) / 10}%` }];
}
