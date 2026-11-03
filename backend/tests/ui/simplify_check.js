// Drives the built app in a headless browser against the shipped sample run (no AI). Prints one JSON object of named checks; tests/test_ui_simplify.py asserts them.
// Usage: node simplify_check.js <base url> [screenshot dir]
const { chromium } = require('playwright');
const base = process.argv[2], shots = process.argv[3] || '';
const R = {};
const ok = (name, cond, detail) => { R[name] = { ok: !!cond, detail: detail === undefined ? '' : String(detail) }; };
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1280, height: 900 } });
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  const shot = async (n) => { if (shots) await p.screenshot({ path: `${shots}/simplify-after-${n}.png` }); };
  await p.goto(base); await p.evaluate(() => localStorage.clear());
  await p.getByRole('button', { name: /Try with sample data/i }).first().click(); await p.waitForSelector('.report-page'); await p.waitForTimeout(800);
  const SUB = ['Your data', 'How files connect', 'What we cleaned', 'How we analysed'];
  const tab = async (name) => {          // the five tabs of the bar; the former setup tabs are sub-views of Data
    if (SUB.includes(name)) { await p.getByRole('tab', { name: /^Data/ }).first().click(); await p.waitForTimeout(300); await p.getByRole('tab', { name: new RegExp('^' + name) }).first().click(); }
    else await p.getByRole('tab', { name: new RegExp('^' + name) }).first().click();
    await p.waitForTimeout(700);
  };
  const applyIfPreview = async () => { const b = p.locator('.lens-bot').last().getByRole('button', { name: 'Apply this change' }); if (await b.count()) { await b.click(); await p.waitForTimeout(1500); } };
  const ask = async (t) => { if (!(await p.locator('.lens-input input').count())) { await p.locator('.lens-fab').click(); await p.waitForTimeout(400); } await p.locator('.lens-input input').first().fill(t); await p.locator('.lens-input button').first().click(); await p.waitForTimeout(1800); await applyIfPreview(); };
  const vis = (sel) => p.locator(sel).evaluateAll(els => els.filter(e => e.offsetParent !== null).length);

  // 1. no controls at rest; short by default; no full/short buttons; no slim bar
  const bad = await p.locator('button, a').evaluateAll(els => els.filter(e => e.offsetParent !== null && /^(Hide|Change|Ask Lens about this|Full view|Short view)$/.test((e.textContent || '').trim())).length);
  ok('no_per_card_buttons', bad === 0, bad);
  ok('no_menu_dots_on_cards', (await vis('.rp-action .lens-more, .rp-chart .lens-more, .rp-tile .lens-more')) === 0);
  ok('no_slim_bar_at_rest', (await vis('.view-bar.slim')) === 0);
  for (const [t, key] of [['Report', 'report'], ['Insights', 'insights'], ['Dashboard', 'dashboard']]) {
    await tab(t);
    const txt = await p.locator('.view-all-link').allInnerTexts();
    ok(`default_short_${key}`, txt.length === 1 && txt[0] === 'Show less', txt.join('|'));          // every tab opens in the full view; the one link offers the short view
    if (key !== 'report') await shot(key);
  }
  await tab('Report'); await shot('report');
  // card controls: at most one primary and one secondary action visible per card
  const cardButtons = await p.locator('.rp-action, .rp-tile, .rp-chart').evaluateAll(els => Math.max(0, ...els.map(e => [...e.querySelectorAll('button, a')].filter(x => x.offsetParent !== null).length)));
  ok('at_most_two_actions_per_report_card', cardButtons <= 2, cardButtons);

  // 2. click to focus, keyboard, clicking elsewhere, removing the chip
  const first = p.locator('.rp-action').first();
  const label = await first.locator('h3').first().innerText();
  await first.click(); await p.waitForTimeout(500);
  const chip = await p.locator('.lens-focus-chip').first().innerText().catch(() => '');
  ok('click_selects_and_opens_lens_with_chip', chip.includes('Focus:') && chip.includes(label.split('\n')[0].trim().slice(0, 10)), chip);
  ok('selected_card_is_outlined', (await p.locator('.rp-action.is-selected').count()) === 1);
  await shot('focus');
  await p.locator('.rp-title').click(); await p.waitForTimeout(300);
  ok('click_elsewhere_clears_focus', (await p.locator('.lens-focus-chip').count()) === 0);
  await p.locator('.rp-chart').first().focus(); await p.keyboard.press('Enter'); await p.waitForTimeout(400);
  ok('enter_selects_a_focused_card', (await p.locator('.lens-focus-chip').count()) === 1 && (await p.locator('.rp-chart.is-selected').count()) === 1);
  ok('cards_are_keyboard_focusable', (await p.locator('.rp-chart').first().getAttribute('tabindex')) === '0');
  await p.locator('.lens-focus-chip button').click(); await p.waitForTimeout(300);
  ok('removing_the_chip_clears_focus', (await p.locator('.lens-focus-chip').count()) === 0 && (await p.locator('.is-selected').count()) === 0);

  // 6. few suggestions: at most 4 without an AI, chosen for the tab, with "More ideas"; gone after the first message
  const starters = await p.locator('.lens-teach > .lens-chips .chip').allInnerTexts();
  ok('first_open_chips_at_most_four_without_an_ai', starters.length >= 2 && starters.length <= 4, starters.join('|'));
  ok('first_open_has_more_ideas_link', (await p.locator('.lens-more-ideas').count()) === 1);
  await p.locator('.lens-more-ideas').first().click(); await p.waitForTimeout(200);
  ok('more_ideas_reveals_more', (await p.locator('.lens-teach .lens-chips').count()) === 2);

  // 3/4/7. chat edits are visible on every tab, through the slim bar, with Undo and Reset
  const edits = [['data', 'hide the cleaned table', 'Your data', 'data:table:clean'], ['cleaning', 'hide the cleaning log', 'What we cleaned', 'cleaning:steps'], ['plan', 'hide how we analysed', 'How we analysed', 'plan:questions'],
                 ['powerbi', 'hide power bi export', 'Export:Power BI files', 'powerbi:pack'], ['chat', 'hide the conversation', 'Lens:expand', 'chat:conversation']];
  for (const [key, phrase, where, id] of edits) {
    await ask(phrase);
    const inDialog = where.startsWith('Export:');
    if (inDialog) { await p.getByRole('button', { name: /^Export/ }).click(); await p.getByRole('menuitem', { name: where.slice(7) }).click(); await p.waitForTimeout(500); }
    else if (where === 'Lens:expand') { await p.locator('.lens-head-actions').getByRole('button', { name: 'Expand' }).click(); await p.waitForTimeout(600); }
    else await tab(where);
    const bar = inDialog ? '.dialog .view-bar.slim' : '.view-bar.slim';
    const hidden = await p.locator(`[data-view-id="${id}"]`).evaluateAll(els => els.length > 0 && els.every(e => e.offsetParent === null));
    ok(`chat_hide_visible_on_${key}`, hidden, id);
    ok(`slim_bar_on_${key}`, (await vis(bar)) === 1, await p.locator(`${bar} .view-bar-text`).first().innerText().catch(() => ''));
    if (key === 'cleaning') await shot('slim-bar');
    await p.locator(bar).getByRole('button', { name: 'Reset' }).click(); await p.waitForTimeout(600);
    ok(`reset_removes_bar_${key}`, (await vis(bar)) === 0);
    ok(`reset_restores_${key}`, await p.locator(`[data-view-id="${id}"]`).evaluateAll(els => els.some(e => e.offsetParent !== null)), id);
    if (inDialog) { await p.keyboard.press('Escape'); await p.waitForTimeout(300); }
  }
  ok('model_tab_items_exist_in_the_catalogue', (await (await p.request.get(base + '/api/runs/5a1e5d3e0001/view')).json()).sections.model.length >= 1);
  // short view on a tab that is not short by default
  await tab('What we cleaned'); await ask('make this tab shorter'); await p.waitForTimeout(400);
  const rows = await vis('[data-view-id^="cleaning:step:"]');
  ok('short_view_on_cleaning', rows === 5 && (await p.locator('.view-all-link').innerText()).startsWith('Show all'), rows);
  await p.locator('.view-all-link').click(); await p.waitForTimeout(500);
  ok('show_all_brings_the_rest_back', (await vis('[data-view-id^="cleaning:step:"]')) > 5);
  ok('showing_all_on_a_default_full_tab_is_the_default_so_no_bar', (await vis('.view-bar.slim')) === 0);

  // hide, then undo, on the report: bar appears and disappears
  await tab('Report'); await ask('hide the appendix'); await ask('undo');
  ok('undo_removes_the_bar', (await vis('.view-bar.slim')) === 0);
  await tab('Insights'); await ask('hide the appendix'); await p.waitForTimeout(300);
  ok('bar_text_counts_hidden', /1 item hidden/.test(await p.locator('.view-bar.slim .view-bar-text').first().innerText().catch(() => '')));
  await p.locator('.view-bar.slim').getByRole('button', { name: 'Undo' }).click(); await p.waitForTimeout(500);
  ok('bar_gone_after_undo', (await vis('.view-bar.slim')) === 0);

  // 5/8. the answer menu and the brief
  ok('starters_removed_after_the_first_message', (await p.locator('.lens-teach').count()) === 0);
  // briefs on the shared sample are read-only for everyone, so the brief steps run on this visitor's own run (made with the same browser session, so it is theirs)
  const csv = require('fs').readFileSync(require('path').join(__dirname, '..', '..', '..', 'sample_data', 'sales.csv'));
  const made = await p.request.post(base + '/api/runs', { multipart: { goal: 'Which segment earns the most revenue?', files: { name: 'sales.csv', mimeType: 'text/csv', buffer: csv } } });
  const own = (await made.json()).run_id;
  for (let i = 0; i < 120; i++) { const st = await (await p.request.get(`${base}/api/runs/${own}`)).json(); if (st.status === 'needs_input') await p.request.post(`${base}/api/runs/${own}/answers`, { data: { skip: true } }); if (['done', 'failed', 'error'].includes(st.status)) break; await p.waitForTimeout(1000); }
  await p.goto(`${base}/#/run/${own}`); await p.reload(); await p.waitForSelector('.report-page', { timeout: 20000 }); await p.waitForTimeout(800);
  await ask('write a brief for the new hire about revenue by segment'); await p.waitForTimeout(500);
  const brief = p.locator('.lens-brief').last();
  const order = await brief.evaluate(el => { const q = el.querySelector('.lens-brief-quality'), h = el.querySelector('.lens-brief-head'); return !!q && !!h && !!(q.compareDocumentPosition(h) & Node.DOCUMENT_POSITION_FOLLOWING); });
  ok('brief_shows_trust_and_quality_note_first', order && /How sure we are/.test(await brief.locator('.lens-brief-quality').innerText()));
  const vb = await brief.locator('button, a').evaluateAll(els => els.filter(e => e.offsetParent !== null).map(e => (e.getAttribute('aria-label') || e.textContent || '').trim()));
  ok('brief_one_primary_one_secondary_at_rest', vb.filter(x => x).length === 2, vb.join('|'));
  await shot('brief');
  await brief.getByRole('button', { name: /More actions for this brief/ }).click(); await brief.getByRole('menuitem', { name: 'Rename' }).click();
  await brief.getByLabel('New title').fill('Note for Priya'); await brief.getByRole('button', { name: 'Save' }).click(); await p.waitForTimeout(600);
  ok('brief_rename', /Note for Priya/.test(await brief.locator('h4').innerText()) && /Edited by you/.test(await brief.locator('h4').innerText()), await brief.locator('h4').innerText());
  await brief.getByRole('button', { name: /More actions for this brief/ }).click(); await brief.getByRole('menuitem', { name: 'Edit' }).click();
  await brief.getByLabel('Edit this line').first().fill('My own headline for Priya.'); await brief.getByRole('button', { name: 'Save changes' }).click(); await p.waitForTimeout(700);
  const headTxt = await brief.locator('.lens-brief-head').innerText();
  ok('brief_edit_is_labelled', /My own headline for Priya\./.test(headTxt) && /Edited by you/.test(headTxt), headTxt);
  ok('brief_quality_note_survives_edit', /How sure we are/.test(await brief.locator('.lens-brief-quality').innerText()));
  const saved = await (await p.request.get(base + `/api/runs/${own}/briefs`)).json();
  ok('brief_saved_with_the_run', saved.briefs.some(x => x.title === 'Note for Priya' && x.headline.edited === true));

  await tab('Report'); await ask('which segment has the highest total revenue'); await p.waitForTimeout(800);
  const ans = p.locator('.lens-bot').last();
  const resting = await ans.locator('button, a').evaluateAll(els => els.filter(e => e.offsetParent !== null).map(e => (e.getAttribute('aria-label') || e.textContent || '').trim()));
  ok('answer_menu_closed_at_rest', !resting.some(x => /^(Expand|Pin to report|Pin to dashboard|Download image|Download table)$/.test(x)), resting.join('|'));
  await ans.getByRole('button', { name: /More actions for this answer/ }).click(); await p.waitForTimeout(200);
  const items = await ans.locator('[role=menuitem]').allInnerTexts();
  ok('answer_menu_has_the_actions', ['Expand', 'Pin to report', 'Pin to dashboard'].every(x => items.includes(x)) && items.some(x => /^Download/.test(x)), items.join('|'));
  ok('answer_keeps_thumbs_and_next_chips', (await ans.locator('.lens-thumbs').count()) === 1 && (await ans.locator('.lens-next .chip').count()) >= 1);
  ok('at_most_three_next_chips', (await ans.locator('.lens-next .chip').count()) <= 3, await ans.locator('.lens-next .chip').count());
  await shot('lens-answer');
  ok('no_page_errors', errs.length === 0, errs.join('; '));
  await b.close();
  console.log(JSON.stringify(R));
})().catch(e => { console.log(JSON.stringify({ crashed: { ok: false, detail: String(e).slice(0, 400), partial: R } })); process.exit(0); });
