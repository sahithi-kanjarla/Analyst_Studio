// Drives the built app in a headless browser against the shipped sample run (no AI): the five tabs, the Data sub-views, the Export menu, Expand all details, Review answers, the logo, old links and widths.
// Prints one JSON object of named checks; tests/test_ui_nav.py asserts them. Usage: node nav_check.js <base url>
const { chromium } = require('playwright');
const base = process.argv[2];
const RUN = '5a1e5d3e0001';
const R = {};
const ok = (name, cond, detail) => { R[name] = { ok: !!cond, detail: detail === undefined ? '' : String(detail) }; };
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 900 } });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  const vis = (sel) => p.locator(sel).evaluateAll(els => els.filter(e => e.offsetParent !== null).length);
  const open = async (hash = '') => { await p.goto(`${base}/#/run/${RUN}${hash}`); await p.reload(); await p.waitForSelector('.runbar', { timeout: 15000 }); await p.waitForTimeout(900); };
  const tab = async (name) => { await p.locator('.runbar-tabs').getByRole('tab', { name: new RegExp('^' + name) }).click(); await p.waitForTimeout(600); };
  const ask = async (t) => { if (!(await p.locator('.lens-input input').count())) { await p.locator('.lens-fab').click(); await p.waitForTimeout(400); } await p.locator('.lens-input input').first().fill(t); await p.locator('.lens-input button').first().click(); await p.waitForTimeout(1800); const ap = p.locator('.lens-bot').last().getByRole('button', { name: 'Apply this change' }); if (await ap.count()) { await ap.click(); await p.waitForTimeout(1500); } };
  await p.goto(base); await p.evaluate(() => localStorage.clear());
  await open();

  // 1. the five tabs, in order, in one thin bar that fits at 1280px
  const names = await p.locator('.runbar-tabs [role=tab]').evaluateAll(els => els.map(e => e.childNodes[0].textContent.trim()));
  ok('five_tabs_in_order', JSON.stringify(names) === JSON.stringify(['Report', 'Insights', 'Ideas to try', 'Dashboard', 'Data']), names.join('|'));
  const geo = await p.evaluate(() => { const r = document.querySelector('.runbar'); const t = document.querySelector('.runbar-tabs'); return { h: r.getBoundingClientRect().height, fits: r.scrollWidth <= r.clientWidth + 1 && t.scrollWidth <= t.clientWidth + 1, page: document.documentElement.scrollWidth - document.documentElement.clientWidth }; });
  ok('bar_is_thin', geo.h >= 44 && geo.h <= 56, geo.h);
  ok('tabs_fit_at_1280_without_scrolling', geo.fits && geo.page <= 0, JSON.stringify(geo));
  ok('old_tabs_are_gone', (await p.locator('.runbar-tabs [role=tab]').filter({ hasText: /Power BI|Chat|Print|Your data|What we cleaned|How we analysed|How files connect/ }).count()) === 0);
  ok('no_print_or_power_bi_buttons_on_the_page', (await p.getByRole('button', { name: /Print \/ Save as PDF/ }).count()) === 0 && (await p.getByText('Download Power BI pack').count()) === 0);
  ok('landing_header_is_not_shown_in_the_analysis_view', (await p.locator('header.topbar').count()) === 0 && (await p.locator('.runbar').count()) === 1);
  const right = await p.locator('.runbar-right').evaluate(el => [...el.querySelectorAll('button, a')].filter(e => e.offsetParent !== null).map(e => (e.getAttribute('aria-label') || e.textContent).trim().replace(/\s*▾$/, '')));
  ok('bar_has_export_review_and_a_menu', right[0] === 'Export' && right.includes('Review answers') && right.includes('More'), right.join('|'));
  await p.locator('.runbar-more button').click(); await p.waitForTimeout(200);
  const more = await p.locator('.runbar-more [role=menuitem]').allInnerTexts();
  ok('menu_has_glossary_and_history', more.includes('Glossary') && more.includes('History'), more.join('|'));
  await p.keyboard.press('Escape');

  // 1b. the Data tab: a visible row of four sub-views, each a full page; the choice is remembered
  await tab('Data');
  const seg = await p.getByRole('tablist', { name: 'Data views' }).getByRole('tab').allInnerTexts();
  ok('data_has_four_visible_subviews', JSON.stringify(seg) === JSON.stringify(['Your data', 'How files connect', 'What we cleaned', 'How we analysed']), seg.join('|'));
  ok('subviews_are_a_segmented_control_not_a_dropdown', (await p.locator('.dv select, .dv details').count()) === 0 && (await vis('.dv .dv-item')) === 4);
  ok('your_data_includes_how_the_data_was_set_up', (await p.getByRole('heading', { name: 'What each column holds' }).isVisible()) && (await vis('.dataviewer')) === 1 && (await vis('.column-meanings tbody tr')) >= 5);
  const sel = { 'How files connect': null, 'What we cleaned': 'cleaning:steps', 'How we analysed': 'plan:questions' };
  for (const [label, id] of Object.entries(sel)) {
    await p.getByRole('tablist', { name: 'Data views' }).getByRole('tab', { name: label }).click(); await p.waitForTimeout(500);
    const shown = id ? (await vis(`[data-view-id="${id}"]`)) >= 1 : (await p.getByText(/no files to connect|link/i).first().isVisible());
    ok(`subview_${label.replace(/ /g, '_')}_is_a_full_page`, shown && (await p.locator('[role=tabpanel]').count()) === 1, id || 'text');
  }
  await p.getByRole('tablist', { name: 'Data views' }).getByRole('tab', { name: 'What we cleaned' }).click(); await p.waitForTimeout(300);
  await open(); await tab('Data');
  ok('chosen_subview_is_remembered', (await p.getByRole('tab', { name: 'What we cleaned' }).getAttribute('aria-selected')) === 'true' && (await vis('[data-view-id="cleaning:steps"]')) >= 1);
  await p.getByRole('tablist', { name: 'Data views' }).getByRole('tab', { name: 'Your data' }).click();

  // 5. every item id is reachable: the sections of each page are on that page (unless the short view folds them)
  const view = await (await p.request.get(`${base}/api/runs/${RUN}/view`)).json();
  const pages = { report: ['Report', null], insights: ['Insights', null], strategy: ['Ideas to try', null], dashboard: ['Dashboard', null], data: ['Data', 'Your data'], cleaning: ['Data', 'What we cleaned'], plan: ['Data', 'How we analysed'] };
  const NEVER_DRAWN = new Set(['strategy:list', 'data:tables', 'plan:definitions']);          // catalogue-only sections: no page element carried these ids before this change either
  const missing = [];
  for (const [bt, [t, sub]] of Object.entries(pages)) {
    await tab(t); if (sub) { await p.getByRole('tablist', { name: 'Data views' }).getByRole('tab', { name: sub }).click(); await p.waitForTimeout(400); }
    const folded = new Set(view.resolved.tabs[bt] ? view.resolved.tabs[bt].folded.concat(view.resolved.tabs[bt].hidden) : []);
    for (const id of view.sections[bt] || []) {
      if (folded.has(id) || NEVER_DRAWN.has(id)) continue;
      if ((await p.locator(`[data-view-id="${id}"]`).count()) === 0) missing.push(id);
    }
  }
  ok('every_section_id_is_on_its_page', missing.length === 0, missing.join(','));
  ok('the_catalogue_still_covers_all_nine_tabs', ['report', 'insights', 'strategy', 'dashboard', 'data', 'model', 'cleaning', 'plan', 'chat'].every(t => (view.sections[t] || []).length >= 1), Object.keys(view.sections).join(','));

  // 2. the Export menu
  await tab('Report');
  await p.getByRole('button', { name: /^Export/ }).click(); await p.waitForTimeout(200);
  const items = await p.locator('.export-menu [role=menuitem]').allInnerTexts();
  ok('export_menu_has_every_export', JSON.stringify(items) === JSON.stringify(['Download PDF', 'Download report.md', 'Download cleaned data', 'Download Power BI pack', 'Briefs', 'Print']), items.join('|'));
  ok('download_pdf_is_the_report_pdf', /\/export\/report$/.test(await p.getByRole('menuitem', { name: 'Download PDF' }).getAttribute('href')));
  ok('power_bi_pack_and_the_other_downloads_are_direct_links', /\/powerbi$/.test(await p.getByRole('menuitem', { name: 'Download Power BI pack' }).getAttribute('href')) && /cleaned\.csv$/.test(await p.getByRole('menuitem', { name: 'Download cleaned data' }).getAttribute('href')) && /report\.md$/.test(await p.getByRole('menuitem', { name: 'Download report.md' }).getAttribute('href')));
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);
  await p.getByRole('button', { name: /^Export/ }).click(); await p.getByRole('menuitem', { name: 'Briefs' }).click(); await p.waitForTimeout(700);
  ok('export_briefs_opens_the_briefs', (await p.getByRole('dialog', { name: /Briefs/ }).isVisible()));
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);
  await p.getByRole('button', { name: /^Export/ }).click(); await p.getByRole('menuitem', { name: 'Print' }).click(); await p.waitForTimeout(500);
  ok('export_print_opens_the_print_options', (await p.getByRole('dialog', { name: /Print/ }).isVisible()) && (await p.getByRole('dialog').getByRole('button', { name: /Print \/ Save as PDF/ }).isVisible()));
  await p.mouse.click(5, 450); await p.waitForTimeout(300);
  ok('export_dialog_closes_on_click_outside', (await p.getByRole('dialog').count()) === 0);
  await p.getByRole('button', { name: /^Export/ }).click(); await p.keyboard.press('Escape'); await p.waitForTimeout(200);
  ok('export_menu_closes_on_escape_and_returns_focus', (await p.locator('.export-menu [role=menu]').count()) === 0 && (await p.evaluate(() => document.activeElement.textContent.startsWith('Export'))));

  // 4. Expand all details: one control per page, remembered, and set by chat
  for (const [t, sub] of [['Report'], ['Insights'], ['Ideas to try'], ['Dashboard'], ['Data', 'What we cleaned'], ['Data', 'How we analysed']]) {
    await tab(t); if (sub) await p.getByRole('tablist', { name: 'Data views' }).getByRole('tab', { name: sub }).click();
    await p.waitForTimeout(300);
    ok(`one_details_control_on_${(sub || t).replace(/ /g, '_')}`, (await vis('.details-toggle')) === 1 && /Expand all details|Collapse details/.test(await p.locator('.details-toggle').innerText()));
  }
  await tab('Report');
  const closed = () => p.locator('.report-page details').evaluateAll(els => els.filter(e => !e.open).length);
  const total = await p.locator('.report-page details').count();
  const before = await closed();
  ok('report_has_folded_supporting_detail', total > 0 && before > 0, `${before}/${total}`);
  const headline = await p.locator('.rp-big').isVisible(), caption = await p.locator('.rp-chart figcaption').first().isVisible(), actions = await vis('.rp-action');
  ok('the_headline_actions_and_chart_captions_are_open_without_expanding', headline && caption && actions >= 1);
  await p.locator('.details-toggle').click(); await p.waitForTimeout(700);
  ok('expand_all_opens_every_fold_and_the_button_changes', (await closed()) === 0 && /Collapse details/.test(await p.locator('.details-toggle').innerText()), `${await closed()}`);
  await open(); await tab('Report');
  ok('expanded_state_is_remembered_for_this_report', (await closed()) === 0 && /Collapse details/.test(await p.locator('.details-toggle').innerText()));
  await tab('Insights');
  ok('expanding_applies_to_every_page', (await p.locator('.insights details').evaluateAll(els => els.filter(e => !e.open).length)) === 0);
  await p.locator('.details-toggle').click(); await p.waitForTimeout(600);
  ok('collapse_folds_them_again', (await p.locator('.insights details').evaluateAll(els => els.filter(e => !e.open).length)) >= 1 && /Expand all details/.test(await p.locator('.details-toggle').innerText()));
  await ask('show all details');
  ok('lens_can_show_all_details_by_chat', /Collapse details/.test(await p.locator('.details-toggle').innerText()) && (await p.locator('.insights details').evaluateAll(els => els.filter(e => !e.open).length)) === 0);
  await ask('collapse details');
  ok('lens_can_collapse_details_by_chat', /Expand all details/.test(await p.locator('.details-toggle').innerText()));
  await p.keyboard.press('Escape'); await p.waitForTimeout(200);

  // 5. Review answers: three ways to close, focus trap, focus returns, asks first when something is unsaved
  await open(); await tab('Report');
  const rev = p.getByRole('button', { name: 'Review answers' });
  await rev.click(); await p.waitForTimeout(500);
  ok('review_opens_as_a_dialog', (await p.getByRole('dialog', { name: /Review answers/ }).isVisible()));
  await p.locator('.dialog-x').click(); await p.waitForTimeout(300);
  ok('review_closes_with_the_x_button', (await p.getByRole('dialog').count()) === 0);
  ok('review_returns_focus_to_its_button', (await p.evaluate(() => document.activeElement && document.activeElement.textContent.trim())) === 'Review answers');
  await rev.click(); await p.waitForTimeout(400); await p.keyboard.press('Escape'); await p.waitForTimeout(300);
  ok('review_closes_with_escape', (await p.getByRole('dialog').count()) === 0);
  await rev.click(); await p.waitForTimeout(400); await p.mouse.click(6, 450); await p.waitForTimeout(300);
  ok('review_closes_with_a_click_outside', (await p.getByRole('dialog').count()) === 0);
  await rev.click(); await p.waitForTimeout(400);
  let trapped = true;
  for (let i = 0; i < 8; i++) { await p.keyboard.press('Tab'); trapped = trapped && (await p.evaluate(() => !!document.activeElement.closest('.dialog'))); }
  for (let i = 0; i < 3; i++) { await p.keyboard.press('Shift+Tab'); trapped = trapped && (await p.evaluate(() => !!document.activeElement.closest('.dialog'))); }
  ok('review_traps_focus_while_open', trapped);
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);

  // a run with answers to change (the sample asks nothing, so the review is served with one question for this check)
  await p.route(`**/api/runs/${RUN}/answers-review`, r => r.fulfill({ json: { workspace: 'w', status: 'done', answers: [{ id: 'q1', text: 'How should missing values be treated?', options: [{ value: 'median', label: 'Fill with the median' }, { value: 'drop', label: 'Leave those rows out' }], default: 'median', answer: 'median', source: 'default', reused: false, saved: '' }] } }));
  await rev.click(); await p.waitForTimeout(600);
  await p.locator('.dialog-x').click(); await p.waitForTimeout(300);
  ok('review_with_no_changes_closes_without_asking', (await p.getByRole('dialog').count()) === 0);
  await rev.click(); await p.waitForTimeout(600);
  await p.getByLabel('Leave those rows out').check(); await p.waitForTimeout(200);
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);
  ok('review_asks_before_closing_with_unsaved_changes_escape', (await p.getByRole('dialog').count()) === 1 && (await p.getByRole('alertdialog', { name: 'Unsaved changes' }).isVisible()));
  await p.getByRole('button', { name: 'Keep editing' }).click(); await p.waitForTimeout(200);
  ok('review_keep_editing_stays_open', (await p.getByRole('dialog').count()) === 1 && (await p.getByRole('alertdialog').count()) === 0);
  await p.mouse.click(6, 450); await p.waitForTimeout(300);
  ok('review_asks_before_closing_with_unsaved_changes_outside_click', (await p.getByRole('alertdialog', { name: 'Unsaved changes' }).isVisible()));
  await p.getByRole('button', { name: 'Keep editing' }).click();
  await p.locator('.dialog-x').click(); await p.waitForTimeout(300);
  ok('review_asks_before_closing_with_unsaved_changes_x', (await p.getByRole('alertdialog', { name: 'Unsaved changes' }).isVisible()));
  // the logo while something is unsaved: ask first
  await p.locator('.runbar-brand').evaluate(e => e.click()); await p.waitForTimeout(400);
  ok('logo_asks_first_when_answers_are_unsaved', (await p.getByRole('dialog', { name: /Leave without saving/ }).isVisible()) && /#\/run\//.test(await p.evaluate(() => location.hash)));
  await p.getByRole('button', { name: 'Stay here' }).click(); await p.waitForTimeout(300);
  ok('stay_keeps_the_report_open', /#\/run\//.test(await p.evaluate(() => location.hash)));
  await p.getByRole('button', { name: 'Discard and close' }).click(); await p.waitForTimeout(300);
  ok('discard_and_close_closes_the_review', (await p.getByRole('dialog').count()) === 0);
  await p.unroute(`**/api/runs/${RUN}/answers-review`);

  // 3. the logo returns to the home page
  await p.locator('.runbar-brand').click(); await p.waitForTimeout(800);
  ok('logo_returns_to_the_home_page', (await p.evaluate(() => location.hash)) === '#/' && (await p.locator('header.topbar').count()) === 1 && (await p.locator('.runbar').count()) === 0);

  // 7. old links
  const old = { powerbi: async () => (await p.getByRole('tab', { name: 'Report' }).getAttribute('aria-selected')) === 'true',
                print: async () => (await p.getByRole('dialog', { name: /Print/ }).isVisible()),
                chat: async () => (await p.locator('[data-view-id="chat:conversation"]').first().isVisible()),
                cleaning: async () => (await p.getByRole('tab', { name: 'What we cleaned' }).getAttribute('aria-selected')) === 'true',
                model: async () => (await p.getByRole('tab', { name: 'How files connect' }).getAttribute('aria-selected')) === 'true',
                plan: async () => (await p.getByRole('tab', { name: 'How we analysed' }).getAttribute('aria-selected')) === 'true',
                data: async () => (await p.getByRole('tab', { name: 'Your data' }).getAttribute('aria-selected')) === 'true' };
  for (const [name, check] of Object.entries(old)) {
    await p.evaluate(() => localStorage.removeItem('analyst-studio.nav.' + '5a1e5d3e0001'));
    await open(`/${name}`); await p.waitForTimeout(600);
    ok(`old_link_${name}_opens_its_new_place`, await check().catch(() => false));
  }

  // 6. widths: 1440 and a tablet and a phone, no sideways page scroll
  for (const [w, h] of [[1440, 900], [1024, 768], [768, 1024], [390, 800]]) {
    await p.setViewportSize({ width: w, height: h }); await open(); await p.waitForTimeout(500);
    const g = await p.evaluate(() => ({ page: document.documentElement.scrollWidth - document.documentElement.clientWidth, tabs: [...document.querySelectorAll('.runbar-tabs [role=tab]')].filter(e => e.offsetParent !== null).length }));
    ok(`no_horizontal_scroll_at_${w}`, g.page <= 0 && g.tabs === 5, JSON.stringify(g));
    if (w === 390) { await tab('Data'); ok('phone_reaches_the_last_tab', (await p.getByRole('tablist', { name: 'Data views' }).isVisible())); }
  }
  ok('no_page_errors', errs.length === 0, errs.join('; '));
  await b.close();
  console.log(JSON.stringify(R));
})().catch(e => { console.log(JSON.stringify({ crashed: { ok: false, detail: String(e).slice(0, 600), partial: R } })); process.exit(0); });
