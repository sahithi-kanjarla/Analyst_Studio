import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { ReportCard as Card } from "../api";
import { planTable } from "../chartsize";
import { humanise, Understanding } from "../display";
import { bigNumber, direction, shareSlices, tableSeries, takeaway, widgetKind } from "../insightwidget";
import { PALETTE } from "./charts";
import { hasWorking, kindOf, LEVEL_CLASS, Table, Working } from "./ReportCard";
import { ViewImg, ViewItem, ViewTitle } from "./view/ViewUI";

const clean = (t: string) => t.replace(/\s*\((?:table|sum|avg|average|mean|count)\)\s*$/i, "").replace(/^\s*(total|sum of)\s+/i, "");
const sentence = (t: string) => (t ? t.charAt(0).toUpperCase() + t.slice(1) : t);

/** A ranking as a compact list: a label, a bar and the figure, in the table's own order. Few rows are all shown; more get "Show all (n)". */
function RankList({ items, valueHeader, title, total }: { items: { label: string; value: number; text: string }[]; valueHeader: string; title: string; total: number }) {
  const [all, setAll] = useState(false);
  const plan = planTable(items, { all, rows: 6 });
  const max = Math.max(...items.map((i) => Math.abs(i.value)), 0.0001);
  const more = total > items.length;
  return (
    <figure className="iw-viz" data-kind="chart">
      <figcaption className="iw-viz-title">{sentence(title)}<small>{more ? `Top ${plan.items.length} of ${total.toLocaleString()} · ` : plan.showAll && !all ? `Top ${plan.items.length} of ${items.length} · ` : `All ${items.length} · `}{valueHeader.toLowerCase()}, highest first</small></figcaption>
      <ol className="iw-rank">
        {plan.items.map((i) => (
          <li key={i.label}>
            <span className="iw-rank-label" title={i.label}>{i.label}</span>
            <span className="iw-rank-bar" aria-hidden><s style={{ width: `${Math.max(2, (Math.abs(i.value) / max) * 100)}%` }} /></span>
            <b className="iw-rank-val">{i.text}</b>
          </li>
        ))}
      </ol>
      {(plan.showAll || all) && <button type="button" className="chip no-print" aria-expanded={all} onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all (${items.length})`}</button>}
    </figure>
  );
}

function ShareBar({ items, title }: { items: { label: string; value: number; text: string }[]; title: string }) {
  const slices = shareSlices(items);
  const sum = slices.reduce((s, x) => s + Math.max(0, x.value), 0) || 1;
  return (
    <figure className="iw-viz" data-kind="chart">
      <figcaption className="iw-viz-title">{sentence(title)}<small>{items.length > 6 ? `Top 5 of ${items.length}, the rest as Other` : `All ${items.length}`} · share of the whole</small></figcaption>
      <div className="iw-share" role="img" aria-label={slices.map((s) => `${s.label} ${s.text}`).join(", ")}>
        {slices.map((s, i) => <i key={s.label} style={{ width: `${(Math.max(0, s.value) / sum) * 100}%`, background: PALETTE[i % PALETTE.length] }} title={`${s.label}: ${s.text}`} />)}
      </div>
      <ul className="iw-legend">{slices.map((s, i) => <li key={s.label}><i style={{ background: PALETTE[i % PALETTE.length] }} />{s.label} <b>{s.text}</b></li>)}</ul>
    </figure>
  );
}

/** One finding as a widget: type, trust and certainty badges, a headline figure, a chart that fits the finding, a one-line takeaway and, when there is one, the action it supports. */
export default function InsightWidget({ c, index, compact = false, understanding, action }: { c: Card; index: number; compact?: boolean; understanding?: Understanding | null; action?: string }) {
  const say = (t: string) => humanise(t, understanding);
  const kind = kindOf(c.tool);
  const series = tableSeries(c.tables[0], c.tool);
  const wk = widgetKind(c.tool, c.tables, c.chart_url);
  const big = bigNumber(c.headline, c.tool);
  const dir = direction(c.headline, c.tool, big?.sign ?? 0);
  const take = takeaway(say(c.why_it_matters), say(c.observation));
  const items = series?.items ?? [];
  const full = !!series && series.total === items.length && items.length >= 2;
  const hi = full ? items.reduce((a, b) => (b.value > a.value ? b : a)) : null;
  const lo = full ? items.reduce((a, b) => (b.value < a.value ? b : a)) : null;
  const tabular = wk === "ranking" || wk === "share";      // a ranking list stands in for its table: the full sortable table sits in "How we got this"
  return (
    <article className="insight iw" data-kind="insight" data-widget={wk} data-index={index}>
      <header className="iw-head">
        <div className="meta">
          {kind && <span className="tag tag-kind">{kind}</span>}
          <span className={LEVEL_CLASS[c.confidence.level] ?? "tag"} title={c.confidence.reasons.join(" ")}>{c.confidence.label} confidence</span>
          {c.kpi_related && <span className="tag tag-kpi">your KPI</span>}
        </div>
        <ViewTitle id={`insights:card:${c.id}`} tag="h4" text={say(c.headline)} />
      </header>

      <div className="iw-body">
        {big && !compact && (
          <div className={`iw-stat ${dir ? `iw-${dir}` : ""}`} data-kind="numbers">
            <span className="iw-stat-val">{dir === "up" ? "▲ " : dir === "down" ? "▼ " : ""}{big.value}</span>
            <span className="iw-stat-label">{big.label}</span>
          </div>
        )}

        <div className="iw-chart">
          {wk === "ranking" && series && <RankList items={items} valueHeader={series.valueHeader} title={clean(say(series.title))} total={series.total} />}
          {wk === "share" && series && <ShareBar items={items} title={clean(say(series.title))} />}
          {wk === "image" && c.chart_url && (
            <ViewItem id={`insights:chart:${c.id}`} className="insight-chart" data-kind="chart"><ViewImg id={`insights:chart:${c.id}`} src={c.chart_url} alt={say(c.title)} /></ViewItem>
          )}
        </div>
      </div>

      {full && hi && lo && hi.label !== lo.label && !compact && (
        <div className="iw-vs" data-kind="comparison" aria-label="Highest and lowest side by side">
          <div className="iw-vs-card"><span className="muted small">Highest</span><b>{hi.label}</b><span>{hi.text}</span></div>
          <div className="iw-vs-card"><span className="muted small">Lowest</span><b>{lo.label}</b><span>{lo.text}</span></div>
        </div>
      )}

      {!compact && take && <p className="iw-take" data-kind="takeaway">{take}</p>}
      {!compact && action && <p className="iw-action" data-kind="action"><span className="iw-chip">What to do</span> {action}</p>}

      {!compact && (hasWorking(c) || c.observation || c.chart_url) && (
        <Working c={c} say={say}>
          {wk !== "image" && c.chart_url && <ViewItem id={`insights:chart:${c.id}`} className="insight-chart" data-kind="chart"><ViewImg id={`insights:chart:${c.id}`} src={c.chart_url} alt={say(c.title)} /></ViewItem>}
          <ReactMarkdown>{say(c.observation)}</ReactMarkdown>
          {c.also.map((a) => (
            <section key={a.id}><h5>Same split, {say(a.target)}</h5><p><strong>{say(a.headline)}.</strong></p><ReactMarkdown>{say(a.observation)}</ReactMarkdown></section>
          ))}
          {[...(tabular ? c.tables : []), ...c.also.flatMap((a) => a.tables)].map((t) => <Table key={t.id} t={t} und={understanding} />)}
        </Working>
      )}
      {!compact && !tabular && c.tables.map((t) => <Table key={t.id} t={t} und={understanding} />)}
    </article>
  );
}
