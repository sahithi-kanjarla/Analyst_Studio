// Screenshots of the pages the ui2 pass changes, at the four widths it is checked at.
//   NODE_PATH=$(npm root -g) node scripts/ui2_shots.js <base url> <out dir> <label> [pages...]
// Pages: landing, Report, Insights, "Ideas to try", Dashboard, Data. Default: all of them.
// Writes <out dir>/<label>/<page>_<width>.png and prints one JSON object per page and width:
// horizontal overflow, any element wider than the viewport, console errors, and tile sizes.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const [base, outDir, label, ...rest] = process.argv.slice(2);
const RUN = '5a1e5d3e0001';
const WIDTHS = [[1280, 720], [1440, 900], [1920, 1080], [390, 844]];
const ALL = ['landing', 'Report', 'Insights', 'Ideas to try', 'Dashboard', 'Data'];
const pages = rest.length ? rest : ALL;
const out = {};

(async () => {
  const b = await chromium.launch();
  for (const [w, h] of WIDTHS) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
    const p = await ctx.newPage();
    const errs = [];
    p.on('pageerror', (e) => errs.push(String(e).slice(0, 160)));
    p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text().slice(0, 160)); });
    for (const page of pages) {
      const key = `${page}|${w}`;
      try {
        if (page === 'landing') {
          await p.goto(`${base}/#/`);
          await p.waitForSelector('.topbar, .brand', { timeout: 20000 });
        } else {
          await p.goto(`${base}/#/run/${RUN}`);
          await p.reload();
          await p.waitForSelector('.runbar', { timeout: 20000 });
          await p.waitForTimeout(700);
          await p.locator('.runbar-tabs').getByRole('tab', { name: new RegExp('^' + page.replace(/ /g, '\\s')) }).click();
        }
        await p.waitForTimeout(1500);
        out[key] = await p.evaluate(() => {
          const vw = document.documentElement.clientWidth;
          const wide = [...document.querySelectorAll('body *')]
            .filter((e) => e.getBoundingClientRect().width > vw + 1 && e.offsetParent !== null)
            .slice(0, 6)
            .map((e) => `${e.tagName.toLowerCase()}.${(e.className || '').toString().split(' ')[0]} ${Math.round(e.getBoundingClientRect().width)}px`);
          const box = (s) => [...document.querySelectorAll(s)].filter((e) => e.offsetParent !== null)
            .map((e) => { const r = e.getBoundingClientRect(); return `${Math.round(r.width)}x${Math.round(r.height)}`; });
          return {
            hscroll: document.documentElement.scrollWidth - vw,
            wideElements: wide,
            tiles: box('.bi-card, .bi-tile, .bi-tile-wrap > .tile'),
            kpis: box('.bi-kpi').length,
            charts: box('.chart svg, .tile-img img').length,
          };
        });
        out[key].errors = errs.slice(0, 4);
        const dir = path.join(outDir, label);
        fs.mkdirSync(dir, { recursive: true });
        await p.screenshot({ path: path.join(dir, `${page.replace(/ /g, '-')}_${w}.png`), fullPage: true });
      } catch (e) {
        out[key] = { crashed: String(e).slice(0, 220) };
      }
    }
    await ctx.close();
  }
  await b.close();
  console.log(JSON.stringify(out, null, 1));
})().catch((e) => { console.log(JSON.stringify({ crashed: String(e) })); process.exit(1); });
