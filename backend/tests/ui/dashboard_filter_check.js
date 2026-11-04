// Drives the built Dashboard in a headless browser against the sample run (no AI): every browser-drawn chart filters the others on click, shows a "Filtered by" chip with a clear
// button, and clears when clicked again; each category has its own colour and the unselected marks drop to about half strength. Prints one JSON object of named checks.
// Usage: node dashboard_filter_check.js <base url> [screenshot dir]
const { chromium } = require('playwright');
const base = process.argv[2], shots = process.argv[3] || '';
const R = {};
const ok = (name, cond, detail) => { R[name] = { ok: !!cond, detail: detail === undefined ? '' : String(detail) }; };
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto(base); await p.evaluate(() => localStorage.clear());
  await p.getByRole('button', { name: /Try with sample data/i }).first().click(); await p.waitForSelector('.report-page'); await p.waitForTimeout(500);
  await p.getByRole('tab', { name: /^Dashboard/ }).first().click(); await p.waitForSelector('.bi-tile .chart svg', { timeout: 15000 }); await p.waitForTimeout(600);
  const kinds = [['columns', 'svg[aria-label^="Columns"]'], ['donut', 'svg[aria-label^="Share ring"]'], ['bars', 'svg[aria-label^="Bars"]']];
  const snapshot = () => p.evaluate(() => ({ kpis: [...document.querySelectorAll('.bi-kpi')].map(e => e.textContent).join('|'), tiles: [...document.querySelectorAll('.bi-tile')].map(e => e.querySelector('svg') ? e.innerText + [...e.querySelectorAll('svg [role=button]')].map(x => x.getAttribute('aria-label')).join(',') : '').join('||'), showing: (document.querySelector('.bi-chips') || {}).textContent || '' }));
  const found = [];
  for (const [kind, sel] of kinds) {
    const svg = p.locator(`.bi-tile ${sel}`).first();
    if (!(await svg.count())) continue;
    found.push(kind);
    const marks = svg.locator('[role="button"]');
    const n = await marks.count();
    ok(`${kind}_has_marks`, n >= 2, n);
    const pick = (i) => (kind === 'donut' ? p.locator('.bi-tile .legend [role="button"]').nth(i) : p.locator(`.bi-tile ${sel}`).first().locator('[role="button"]').nth(i));          // a ring slice's box centre is its hole, so a ring is clicked on its legend row
    if (kind === 'donut') await p.locator('.bi-tile svg[aria-label^="Share ring"] path').nth(1).dispatchEvent('click'), await p.waitForTimeout(300), await p.locator('.bi-chips .bi-clear').click(), await p.waitForTimeout(300);          // and a click on the slice itself
    const before = await snapshot();
    const fills = await marks.evaluateAll(els => els.map(e => (e.querySelector('rect.bar') || e).getAttribute('fill')));
    if (kind !== 'columns') ok(`${kind}_categories_have_their_own_colours`, new Set(fills).size >= Math.min(n, 3), fills.join(','));
    else ok('columns_keep_one_accent', new Set(fills).size === 1, fills.join(','));
    await pick(1).click(); await p.waitForTimeout(400);
    const after = await snapshot();
    ok(`${kind}_click_shows_filtered_by_chip`, /Filtered by/.test(after.showing) && (await p.locator('.bi-chips .bi-chip').count()) >= 2, after.showing);
    ok(`${kind}_click_filters_other_charts_or_figures`, (await p.locator('.bi-chips').count()) === 1 && /showing [\d,]+ of [\d,]+/.test(after.showing) && (after.kpis !== before.kpis || after.tiles !== before.tiles), after.showing);
    const ops = await marks.evaluateAll(els => els.map(e => Number(e.getAttribute('opacity') ?? 1)));
    ok(`${kind}_selected_full_others_about_half`, ops.filter(o => o === 1).length === 1 && ops.filter(o => o >= 0.5 && o <= 0.56).length === ops.length - 1, ops.join(','));
    ok(`${kind}_selected_label_shown`, (await p.locator('.chart-pick').count()) >= 1);
    ok(`${kind}_key_figures_or_charts_changed`, after.kpis !== before.kpis || after.tiles !== before.tiles);
    await pick(1).click(); await p.waitForTimeout(400);
    ok(`${kind}_second_click_clears`, (await p.locator('.bi-chips').count()) === 0);
    await pick(1).click(); await p.waitForTimeout(300);
    await p.locator('.bi-chips .bi-clear').click(); await p.waitForTimeout(300);
    ok(`${kind}_clear_button_clears`, (await p.locator('.bi-chips').count()) === 0);
  }
  ok('at_least_one_chart_kind_found', found.length >= 1, found.join(','));
  if (shots) { await p.screenshot({ path: `${shots}/dashboard-filter.png`, fullPage: false }); }
  ok('no_page_errors', errs.length === 0, errs.join('|'));
  console.log(JSON.stringify(R));
  await b.close();
})().catch(e => { console.log(JSON.stringify({ crashed: { ok: false, detail: String(e) } })); process.exit(0); });
