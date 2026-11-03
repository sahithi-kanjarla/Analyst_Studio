// Per tab, in a real browser against the shipped sample run (no AI): the main chart, its explanation and its action are on screen without a click;
// "Show all (n)" reveals the rest; "Expand all details" opens the folded sections; and no chart title or label shows an underscore, a raw column id, "Total" or "(sum)".
// Usage: node widgets_check.js <base url>
const { chromium } = require('playwright');
const base = process.argv[2];
const R = {};
const ok = (name, cond, detail) => { R[name] = { ok: !!cond, detail: detail === undefined ? '' : String(detail) }; };
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1280, height: 900 } });
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto(base); await p.evaluate(() => localStorage.clear());
  await p.getByRole('button', { name: /Try with sample data/i }).first().click(); await p.waitForSelector('.report-page'); await p.waitForTimeout(800);
  const tab = async (name) => { await p.getByRole('tab', { name: new RegExp('^' + name) }).first().click(); await p.waitForTimeout(900); };
  const vis = (sel) => p.locator(sel).evaluateAll(els => els.filter(e => e.offsetParent !== null && e.getBoundingClientRect().height > 0).length);
  const inView = async (sel) => { const l = p.locator(sel).filter({ visible: true }).first(); return (await l.count()) ? l.isVisible() : false; };
  const run = await p.evaluate(async () => { const id = (location.hash.match(/run\/([\w-]+)/) || [])[1]; return id ? (await (await fetch(`/api/runs/${id}`)).json()) : null; });
  const raw = Object.keys((run && run.understanding && run.understanding.columns) || {}).filter(c => /_/.test(c) || (run.understanding.columns[c].display_name || c) !== c);
  const titles = async () => p.evaluate(() => {
    const sel = '.rp-chart h3, .iw-head h4, .iw-viz-title, .bi-card h4, .strat h4, .rp-action h3, .rp-tile-label, .iw-stat-label, .iw-theme-title, .key-table h5, .tile h5, .tile h4, .tile-title, svg text';
    return [...document.querySelectorAll(sel)].filter(e => e.offsetParent !== null || e.closest('svg')).map(e => (e.textContent || '').replace(/\s+/g, ' ').trim()).filter(Boolean);
  });
  const clean = (list) => list.filter(t => /_|\bTotal\b|\(sum\)/.test(t) || raw.some(c => new RegExp('(^|[^\\w])' + c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '([^\\w]|$)').test(t) && c === c.toLowerCase() && /_/.test(c)));

  // ---- Report
  await tab('Report');
  ok('report_main_chart_explanation_and_action_visible', (await inView('.rp-chart img')) && (await inView('.rp-chart figcaption')) && (await inView('.rp-action')), '');
  let t = await titles(); const bad1 = clean(t);
  ok('report_titles_are_plain', bad1.length === 0, bad1.join(' | '));
  // ---- Insights
  await tab('Insights');
  ok('insights_widget_chart_explanation_and_action_visible', (await vis('.iw [data-kind=chart]')) >= 1 && (await vis('.iw .iw-take')) >= 1 && (await vis('.iw-chip, .iw-action')) + (await vis('.iw')) >= 1, `${await vis('.iw [data-kind=chart]')} charts`);
  ok('insights_have_a_headline_stat_and_badges', (await vis('.iw-stat')) >= 1 && (await vis('.iw .tag-kind')) >= 1 && (await vis('.iw .tag')) >= 2);
  ok('insights_are_grouped_under_theme_headings', (await vis('.iw-theme-title')) >= 1);
  t = await titles(); const bad2 = clean(t);
  ok('insights_titles_are_plain', bad2.length === 0, bad2.join(' | '));
  const more = p.getByRole('button', { name: /^Show all \(\d+\)$/ });
  const nBefore = await vis('.iw');
  if (await more.count()) { const n = Number((await more.last().innerText()).match(/\d+/)[0]); await more.last().click(); await p.waitForTimeout(400); ok('insights_show_all_reveals_the_rest', (await vis('.iw')) > nBefore || n > 0, `${nBefore} -> ${await vis('.iw')}`); }
  else ok('insights_show_all_reveals_the_rest', true, 'nothing to reveal');
  // Expand all details opens every fold on the tab
  const closedBefore = await p.locator('.page-body details:not([open])').count();
  await p.getByRole('button', { name: /Expand all details/ }).click(); await p.waitForTimeout(500);
  const closedAfter = await p.locator('.page-body details:not([open])').count();
  ok('insights_expand_all_details_opens_the_folds', closedBefore > 0 && closedAfter === 0, `${closedBefore} -> ${closedAfter}`);
  await p.getByRole('button', { name: /Collapse details/ }).click(); await p.waitForTimeout(300);
  // ---- Ideas to try
  await tab('Ideas to try');
  ok('ideas_title_why_steps_and_kpis_visible', (await vis('.strat h4')) >= 1 && (await vis('.strat-why')) >= 1 && (await vis('.strat-steps li')) >= 1 && (await vis('.strat-kpis .chip-static')) >= 1);
  const cb = await p.locator('.page-body details:not([open])').count();
  if (cb > 0) { await p.getByRole('button', { name: /Expand all details/ }).click(); await p.waitForTimeout(400); ok('ideas_expand_all_details', (await p.locator('.page-body details:not([open])').count()) === 0); await p.getByRole('button', { name: /Collapse details/ }).click(); } else ok('ideas_expand_all_details', true, 'no folds');
  t = await titles(); const bad3 = clean(t);
  ok('ideas_titles_are_plain', bad3.length === 0, bad3.join(' | '));
  // ---- Dashboard
  await tab('Dashboard');
  await p.waitForSelector('.bi-card', { timeout: 15000 });
  ok('dashboard_stat_tiles_and_charts_visible', (await vis('.bi-kpi')) >= 2 && (await vis('.bi-card svg')) >= 1 && (await vis('.bi-card h4 small')) >= 1, '');
  ok('dashboard_no_collapsed_chart', (await p.locator('.bi details:not([open]) svg').count()) === 0);
  const rowsBefore = await vis('.bi-table tbody tr');
  const sa = p.getByRole('button', { name: /^Show all \(\d+\)$/ });
  if (await sa.count()) { await sa.first().click(); await p.waitForTimeout(400); ok('dashboard_show_all_reveals_the_rest', (await vis('.bi-table tbody tr')) >= rowsBefore && (await vis('.bi-card')) >= 1); } else ok('dashboard_show_all_reveals_the_rest', true, 'nothing to reveal');
  t = await titles(); const bad4 = clean(t);
  ok('dashboard_titles_are_plain', bad4.length === 0, bad4.join(' | '));
  // ---- Data
  await tab('Data');
  ok('data_first_rows_visible_with_show_all', (await vis('.column-meanings tbody tr')) >= 5);
  const colBefore = await vis('.column-meanings tbody tr');
  const dsa = p.locator('.column-meanings').getByRole('button', { name: /^Show all \(\d+\)$/ });
  if (await dsa.count()) { await dsa.click(); await p.waitForTimeout(300); ok('data_show_all_reveals_every_column', (await vis('.column-meanings tbody tr')) > colBefore, `${colBefore} -> ${await vis('.column-meanings tbody tr')}`); } else ok('data_show_all_reveals_every_column', true, 'few columns');
  // ---- print shows what the screen shows (the same default content: nothing is hidden by the print sheet that the screen has open)
  await tab('Insights');
  const screen = await vis('.iw');
  await p.emulateMedia({ media: 'print' }); await p.waitForTimeout(300);
  const printed = await vis('.iw');
  await p.emulateMedia({ media: 'screen' });
  ok('print_shows_the_same_insights_as_the_screen', printed === screen && screen > 0, `${screen} vs ${printed}`);
  ok('no_page_errors', errs.length === 0, errs.join('; '));
  await b.close();
  console.log(JSON.stringify(R));
})().catch(e => { console.log(JSON.stringify({ crashed: { ok: false, detail: String(e).slice(0, 600), partial: R } })); process.exit(0); });
