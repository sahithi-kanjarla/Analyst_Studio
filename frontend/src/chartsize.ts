/** How many of a chart's values to draw, decided from the chart's own type, its data and the width it is drawn at.
 *
 * One helper for every chart in the app, so no chart carries a rule of its own:
 *   ranked categories  top N by value (10 wide, 8 at laptop width, 5 on a phone), all of them when there are few;
 *                      the rest pooled into one "Other" bar only when the measure adds up. Always a "Show all (n)".
 *   time               every period in the range, never cut; many points are stepped up to a longer period instead.
 *   share of a whole   a few slices plus "Other", and only when the measure adds up and nothing is negative.
 *   points and cells   all of them, sampled only when there are far too many to draw, and the subtitle says so.
 *   a table by a chart a few rows, the rest behind "Show all (n)", in the chart's own order.
 *
 * Nothing here computes a figure: it chooses how many of the figures already computed to show, and the sentence that
 * says which ones those are. */

/** A measure that can be added up (a total) can honestly be pooled into "Other". An average, a rate or a ratio cannot. */
export type Measure = "additive" | "rate";
export type Step = "day" | "week" | "month" | "quarter";

export interface Value {
  key: string;
  label: string;
  value: number;
  n?: number;
}

export interface Pooled extends Value {
  other: true;
  groups: number;
}

export interface Plan<T> {
  /** What to draw, in the order given, with the pooled "Other" last when there is one. */
  items: T[];
  shown: number;
  total: number;
  hidden: number;
  /** Offer a "Show all (n)" control: there is more than is drawn. */
  showAll: boolean;
  /** One line under the title saying exactly what is on show. */
  subtitle: string;
  pooled: boolean;
}

export const FEW = 12;                 // at most this many categories: draw all of them, no pooling, no cutting
export const SHARE_SLICES = 6;         // a share chart stays readable to about here, "Other" included
export const TABLE_ROWS = 6;           // rows of a chart's table visible before "Show all (n)"
export const POINTS_MAX = 3000;        // points or cells beyond this are sampled, and the subtitle says so
export const SERIES_MAX = 60;          // more periods than this and the step moves up (day -> week -> month -> quarter)

/** Top N for ranked categories at this width. Narrow screens get fewer bars, never a cut that hides the ranking. */
export function rankedLimit(width: number): number {
  if (width < 700) return 5;
  if (width < 1280) return 8;
  return 10;
}

const lower = (s: string) => (s && s === s.toUpperCase() && s.length > 4 ? s : s.charAt(0).toLowerCase() + s.slice(1));

/** "region" -> "regions", "city" -> "cities", "Dept" -> "Depts". Used only for the subtitle's wording. */
export function plural(word: string): string {
  const w = (word || "").trim();
  if (!w) return "groups";
  if (/(ss|us|sh|ch|x|z)$/i.test(w)) return w + "es";      // class -> classes, status -> statuses, box -> boxes
  if (/s$/i.test(w)) return w;                             // already plural: orders, months
  if (/[^aeiou]y$/i.test(w)) return w.slice(0, -1) + "ies";
  return w + "s";
}

/** Ranked categories (a bar chart). Few categories are all drawn; otherwise the top N, with the rest pooled only when the measure adds up. */
export function planRanked<T extends Value>(
  values: T[],
  opts: { width: number; measure: Measure; group: string; measureLabel: string },
): Plan<T | Pooled> {
  const total = values.length;
  const limit = rankedLimit(opts.width);
  if (total <= FEW) {
    return { items: [...values], shown: total, total, hidden: 0, showAll: false, pooled: false, subtitle: subtitleAll(total, opts.group) };
  }
  const shownValues = values.slice(0, limit);
  const rest = values.slice(limit);
  const items: (T | Pooled)[] = [...shownValues];
  let pooled = false;
  if (opts.measure === "additive" && rest.length) {
    items.push({
      key: "__other", other: true, groups: rest.length,
      label: `Other (${rest.length} ${rest.length === 1 ? "item" : "items"})`,
      value: rest.reduce((s, v) => s + v.value, 0),
      n: rest.every((v) => v.n === undefined) ? undefined : rest.reduce((s, v) => s + (v.n ?? 0), 0),
    });
    pooled = true;
  }
  return {
    items, shown: shownValues.length, total, hidden: rest.length, showAll: true, pooled,
    subtitle: `Top ${shownValues.length} of ${total} ${plural(lower(opts.group))} by ${lower(opts.measureLabel)}`
      + (pooled ? ", the rest pooled as Other" : ""),
  };
}

function subtitleAll(total: number, group: string): string {
  return `All ${total} ${plural(lower(group))}`;
}

/** A share of a whole (donut). Only honest for a measure that adds up with nothing negative; otherwise the caller should rank bars instead. */
export function planShare<T extends Value>(
  values: T[],
  opts: { measure: Measure; group: string; measureLabel: string },
): Plan<T | Pooled> & { suitable: boolean } {
  const total = values.length;
  const negative = values.some((v) => v.value < 0);
  const sum = values.reduce((s, v) => s + v.value, 0);
  const suitable = opts.measure === "additive" && !negative && sum > 0;
  if (!suitable) {
    return {
      items: [], shown: 0, total, hidden: total, showAll: false, pooled: false, suitable: false,
      subtitle: negative || sum <= 0 ? "Shares need parts that add up to a positive whole" : "Shares only work for a measure that adds up",
    };
  }
  if (total <= SHARE_SLICES) {
    return { items: [...values], shown: total, total, hidden: 0, showAll: false, pooled: false, suitable: true, subtitle: subtitleAll(total, opts.group) };
  }
  const keep = values.slice(0, SHARE_SLICES - 1);
  const rest = values.slice(SHARE_SLICES - 1);
  const items: (T | Pooled)[] = [...keep, {
    key: "__other", other: true, groups: rest.length,
    label: `Other (${rest.length} ${rest.length === 1 ? "item" : "items"})`,
    value: rest.reduce((s, v) => s + v.value, 0),
  }];
  return {
    items, shown: keep.length, total, hidden: rest.length, showAll: true, pooled: true, suitable: true,
    subtitle: `Top ${keep.length} of ${total} ${plural(lower(opts.group))} by share of ${lower(opts.measureLabel)}, the rest pooled as Other`,
  };
}

const STEP_UP: Record<Step, Step | null> = { day: "week", week: "month", month: "quarter", quarter: null };

/** The period to show a time chart by: the one given, stepped up while there would be more points than can be read. Never drops a period. */
export function timeStep(points: number, step: Step = "day"): Step {
  let at: Step = step;
  let n = points;
  const divisor: Record<Step, number> = { day: 7, week: 4.35, month: 3, quarter: 1 };
  while (n > SERIES_MAX && STEP_UP[at]) {
    n = n / divisor[at];
    at = STEP_UP[at]!;
  }
  return at;
}

/** A time chart. Every period in the range is kept; only the step changes, and the subtitle names it. */
export function planSeries<T extends Value>(
  values: T[],
  opts: { step?: Step; stepped?: Step },
): Plan<T> & { step: Step } {
  const total = values.length;
  const step = opts.stepped ?? timeStep(total, opts.step ?? "day");
  const changed = step !== (opts.step ?? "day");
  return {
    items: [...values], shown: total, total, hidden: 0, showAll: false, pooled: false, step,
    subtitle: `All ${total} ${plural(step)}` + (changed ? `, grouped by ${step} so every period fits` : ""),
  };
}

/** Points of a scatter, bins of a distribution or cells of a heatmap: all of them, unless there are far too many to draw. */
export function planPoints<T>(values: T[], opts: { what?: string } = {}): Plan<T> & { sampled: boolean } {
  const total = values.length;
  const what = opts.what ?? "points";
  if (total <= POINTS_MAX) {
    return { items: [...values], shown: total, total, hidden: 0, showAll: false, pooled: false, sampled: false, subtitle: `All ${total.toLocaleString()} ${what}` };
  }
  const every = Math.ceil(total / POINTS_MAX);
  const items = values.filter((_, i) => i % every === 0);
  return {
    items, shown: items.length, total, hidden: total - items.length, showAll: false, pooled: false, sampled: true,
    subtitle: `${items.length.toLocaleString()} of ${total.toLocaleString()} ${what}, evenly sampled so the chart stays readable`,
  };
}

/** The table beside a chart: a few rows in the chart's own order, the rest behind "Show all (n)". */
export function planTable<T>(rows: T[], opts: { all?: boolean; rows?: number } = {}): Plan<T> {
  const total = rows.length;
  const limit = opts.rows ?? TABLE_ROWS;
  const items = opts.all ? [...rows] : rows.slice(0, limit);
  return {
    items, shown: items.length, total, hidden: total - items.length, showAll: total > limit, pooled: false,
    subtitle: total > items.length ? `Showing ${items.length} of ${total}` : `All ${total}`,
  };
}
