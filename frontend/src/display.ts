/** Plain words for things the data named. Nothing here is specific to any column, file or industry: every name comes from
 * the run's own understanding record, or is tidied by shape alone.
 *
 *   displayName   the understanding record's name for a column, else the column's own name made readable
 *   prettify      "sub_category" -> "Sub category", "discount_pct" -> "Discount %", "qty" -> "Quantity"
 *   humanise      the same, inside a sentence the analysis wrote, so no internal id is ever on screen
 *   chartTitle    "Revenue by region", never "Total revenue by region (sum)"
 */

export interface Understanding {
  columns?: Record<string, { display_name?: string; meaning?: string }>;
}

/** Short forms that a spreadsheet writes but a reader should not have to decode. Matched whole-word only. */
const WORDS: Record<string, string> = {
  pct: "%", percent: "%", pc: "%",
  qty: "quantity", qtys: "quantities", amt: "amount", amts: "amounts",
  avg: "average", med: "median", std: "spread", num: "number", no: "number", nos: "numbers",
  rev: "revenue", cust: "customer", custs: "customers", prod: "product", prods: "products",
  cat: "category", cats: "categories", subcat: "sub category", desc: "description",
  id: "ID", ids: "IDs", dt: "date", ts: "time", yr: "year", mo: "month", wk: "week",
  gst: "GST", mrp: "MRP", sku: "SKU", skus: "SKUs", kpi: "KPI", kpis: "KPIs", usd: "USD", inr: "INR",
};

const ACRONYM = /^(?:ID|IDs|GST|MRP|SKU|SKUs|KPI|KPIs|USD|INR|%)$/;

/** A column's own name made readable: separators become spaces, short forms are spelled out, the first letter is a capital. */
export function prettify(name: string): string {
  const raw = String(name ?? "").trim();
  if (!raw) return "";
  const parts = raw
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")        // camelCase -> camel Case
    .split(/[_\-.\s]+/)
    .filter(Boolean)
    .map((w) => {
      const key = w.toLowerCase();
      return WORDS[key] ?? w;
    });
  if (!parts.length) return raw;
  const joined = parts
    .map((w, i) => (ACRONYM.test(w) || w === "%" ? w : i === 0 ? w.charAt(0).toUpperCase() + w.slice(1).toLowerCase() : w.toLowerCase()))
    .join(" ")
    .replace(/\s+%/g, " %");
  return joined.trim();
}

/** What to call a column on screen: what the analysis worked out it means, else its own name made readable. */
export function displayName(col: string, und?: Understanding | null): string {
  const given = und?.columns?.[col]?.display_name;
  const name = (given && given.trim()) || prettify(col);
  return name === col ? prettify(col) : name;
}

/** Column names longest first, so "sub_category" is replaced before "category". */
function namesByLength(und?: Understanding | null): string[] {
  return Object.keys(und?.columns ?? {}).sort((a, b) => b.length - a.length);
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** A sentence the analysis wrote, with every column it names put into plain words. Figures and everything else are untouched. */
export function humanise(text: string, und?: Understanding | null): string {
  let out = String(text ?? "").replace(/`/g, "");
  if (!out) return out;
  for (const col of namesByLength(und)) {
    const name = displayName(col, und);
    if (!name || name === col) continue;
    out = out.replace(new RegExp(`(^|[^\\w])${escape(col)}(?=[^\\w]|$)`, "g"), (_m, before) => `${before}${name}`);
  }
  // any internal name left over (a column the record never described) still reads as words, never as an id
  return out.replace(/\b[a-z0-9]+(?:_[a-z0-9]+)+\b/gi, (m) => prettify(m));
}

/** A chart's title in plain language: what is measured, and how it is split. Never "Total x by y (sum)". */
export function chartTitle(measureLabel: string, group?: string, und?: Understanding | null): string {
  const measure = humanise(String(measureLabel ?? "").replace(/^\s*(total|sum(?: of)?|average|avg(?: of)?|mean(?: of)?)\s+/i, "").replace(/\s*\((?:sum|avg|average|mean|count)\)\s*$/i, ""), und).trim();
  const head = measure ? measure.charAt(0).toUpperCase() + measure.slice(1) : "";
  if (!group) return head;
  const by = displayName(group, und);
  return `${head} by ${by.charAt(0).toLowerCase() + by.slice(1)}`;
}

/** True when a piece of text would show the reader an internal name: an underscore id, or a stray aggregation word. */
export function looksInternal(text: string): boolean {
  return /\b[a-z0-9]+_[a-z0-9_]+\b/i.test(String(text ?? "")) || /\((?:sum|avg|mean|count)\)/i.test(String(text ?? ""));
}

/** A stat or axis label without the aggregation word in front: "Total revenue" reads "Revenue". Only the leading word goes; the figure is untouched. */
export function plainLabel(label: string): string {
  const t = String(label ?? "").replace(/^\s*(total|sum of)\s+/i, "").replace(/\s*\((?:sum|avg|average|mean|count)\)\s*$/i, "").trim();
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : t;
}
