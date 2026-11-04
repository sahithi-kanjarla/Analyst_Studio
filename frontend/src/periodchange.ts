/** "Latest period vs previous" for the Dashboard key figures, without ever comparing a total over part of a month with a total over a whole one.
 *  The same rules as the report's partial-period check (backend/app/partial.py): a last period reached on under 80% of its days is incomplete; an incomplete
 *  period is compared per day (average per day) with the previous full period, and with fewer than 7 days of data no direction is shown at all.
 *  Nothing here knows a column or a file: it works from period keys ("2026-12" for a month), the last date in the rows and whether the measure adds up. */

export const PARTIAL_BELOW = 0.8;
export const MIN_DAYS = 7;

export interface PeriodValue { key: string; value: number }
export interface LatestChange {
  /** "total": both periods complete, compared as they are. "per_day": the last period is incomplete, compared per day. "too_few": incomplete and under MIN_DAYS days, no direction. */
  kind: "total" | "per_day" | "too_few";
  change: number | null;          // the relative change, or null when no direction is shown
  days: number | null;            // days of the last period the data covers (months only)
  of: number | null;              // days in that period
}

const daysInMonth = (key: string): number => {
  const [y, m] = key.split("-").map(Number);
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
};

/** `periods` sorted oldest first; `grain` "month" or "day"; `dataEnd` the last date ("YYYY-MM-DD") among the rows shown; `additive` true when the measure is a total (a sum),
 *  false for an average or a ratio, which a part-period does not distort and so is compared as it is. Null when there is nothing to compare. */
export function latestChange(periods: PeriodValue[], grain: "month" | "day", dataEnd: string | null, additive: boolean): LatestChange | null {
  const last = periods[periods.length - 1], prev = periods[periods.length - 2];
  if (!last || !prev || !prev.value) return null;
  const rel = (a: number, b: number) => (a - b) / Math.abs(b);
  if (grain !== "month" || !dataEnd || dataEnd.slice(0, 7) !== last.key) return { kind: "total", change: rel(last.value, prev.value), days: null, of: null };
  const of = daysInMonth(last.key);
  const days = Math.min(Math.max(Number(dataEnd.slice(8, 10)), 1), of);
  if (days / of >= PARTIAL_BELOW) return { kind: "total", change: rel(last.value, prev.value), days, of };
  if (days < MIN_DAYS) return { kind: "too_few", change: null, days, of };
  if (!additive) return { kind: "per_day", change: rel(last.value, prev.value), days, of };
  return { kind: "per_day", change: rel(last.value / days, prev.value / daysInMonth(prev.key)), days, of };
}
