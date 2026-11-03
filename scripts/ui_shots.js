// Screenshots and a layout audit of every tab, in the same headless browser the UI tests use.
//   node scripts/ui_shots.js <base url> <out dir> <label> <runId>[:<name>] ...
// For each run, each tab and each of 1440 / 1024 / 390 px it writes <out dir>/<label>/<name>_<tab>_<width>.png
// and prints one JSON object: what is folded away, what needs a click, every chart title, over-long text and horizontal scrolling.
// Needs the global playwright the tests use: NODE_PATH=$(npm root -g).
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const [base, outDir, label, ...runArgs] = process.argv.slice(2);
const WIDTHS = [1440, 1024, 390];
const TABS = ['Report', 'Insights', 'Ideas to try', 'Dashboard', 'Data'];
const SUBVIEWS = ['Your data', 'How files connect', 'What we cleaned', 'How we analysed'];
const LONG_TEXT = 320;              // a paragraph above the fold longer than this reads as a wall of text
const BAD_TITLE = /_|\(sum\)|\bsum\b|\bavg\b|\bpct\b|\bqty\b|\bcol\b|\bTotal [A-Z]/;

const runs = runArgs.map((a) => { const [id, name] = a.split(':'); return { id, name: name || id }; });
const out = {};

(async () => {
  const b = await chromium.launch();
  for (const run of runs) {
    for (const w of WIDTHS) {
      const ctx = await b.newContext({ viewport: { width: w, height: 900 }, deviceScaleFactor: 1 });
      const p = await ctx.newPage();
      const errs = [];
      p.on('pageerror', (e) => errs.push(String(e)));
      await p.goto(`${base}/#/run/${run.id}`);
      await p.evaluate(() => window.localStorage.clear());

      for (const tab of TABS) {
        const subs = tab === 'Data' ? SUBVIEWS : [null];
        for (const sub of subs) {
          const key = `${run.name}|${sub || tab}|${w}`;
          try {
            await p.goto(`${base}/#/run/${run.id}`);
            await p.reload();
            await p.waitForSelector('.runbar', { timeout: 20000 });
            await p.waitForTimeout(700);
            await p.locator('.runbar-tabs').getByRole('tab', { name: new RegExp('^' + tab.replace(/ /g, '\\s')) }).click();
            await p.waitForTimeout(900);
            if (sub) {
              await p.getByRole('tablist', { name: 'Data views' }).getByRole('tab', { name: sub }).click();
              await p.waitForTimeout(900);
            }
            await p.waitForTimeout(600);        // charts and the dashboard fetch their own data

            const audit = await p.evaluate((LONG) => {
              const seen = (el) => el && el.offsetParent !== null;
              const text = (el) => (el ? (el.innerText || '').trim().replace(/\s+/g, ' ') : '');
              const body = document.querySelector('.page-body, .results') || document.body;
              const folds = [...body.querySelectorAll('details')].map((d) => ({
                summary: text(d.querySelector('summary')).slice(0, 90),
                open: !!d.open,
                charts: d.querySelectorAll('img, svg').length,
                tables: d.querySelectorAll('table').length,
                chars: text(d).length,
              }));
              const buttons = [...body.querySelectorAll('button, a')].filter(seen).map((x) => text(x)).filter((t) => /show all|show more|show less|expand|view more|more charts/i.test(t));
              const charts = [...body.querySelectorAll('img, svg')].map((c) => {
                const card = c.closest('.bi-card, .insight, .view-card, figure, section');
                const h = card ? card.querySelector('h3, h4, h5') : null;
                const small = card ? card.querySelector('h4 small, .bi-card h4 small') : null;
                return { title: text(h).slice(0, 110), sub: text(small).slice(0, 110), visible: seen(c), inClosedFold: !!c.closest('details:not([open])'), tag: c.tagName.toLowerCase() };
              });
              const heads = [...body.querySelectorAll('h1,h2,h3,h4,h5')].filter(seen).map((h) => text(h).slice(0, 110));
              const paras = [...body.querySelectorAll('p, li')].filter(seen).map((x) => text(x)).filter((t) => t.length > LONG);
              return {
                folds, buttons, charts, heads,
                longText: paras.slice(0, 8).map((t) => ({ chars: t.length, start: t.slice(0, 110) })),
                visibleCharts: charts.filter((c) => c.visible && !c.inClosedFold).length,
                hiddenCharts: charts.filter((c) => !c.visible || c.inClosedFold).length,
                tiles: body.querySelectorAll('.kpi, .bi-kpi, .rp-tile, .tile').length,
                tables: [...body.querySelectorAll('table')].filter(seen).map((t) => t.querySelectorAll('tbody tr').length),
                badges: [...body.querySelectorAll('.tag, .bi-tag, .rp-pill, .badge')].filter(seen).length,
                hscroll: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                chars: text(body).length,
              };
            }, LONG_TEXT);

            audit.badTitles = [...audit.heads, ...audit.charts.map((c) => c.title), ...audit.charts.map((c) => c.sub)]
              .filter((t) => t && BAD_TITLE.test(t)).slice(0, 12);
            audit.foldedAway = audit.folds.filter((f) => !f.open).map((f) => `${f.summary} [${f.charts} charts, ${f.tables} tables, ${f.chars} chars]`);
            audit.errors = errs.slice(0, 3);
            out[key] = audit;

            const dir = path.join(outDir, label);
            fs.mkdirSync(dir, { recursive: true });
            const file = `${run.name}_${(sub || tab).replace(/ /g, '-')}_${w}.png`;
            await p.screenshot({ path: path.join(dir, file), fullPage: true });
          } catch (e) {
            out[key] = { crashed: String(e).slice(0, 220) };
          }
        }
      }
      await ctx.close();
    }
  }
  await b.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.log(JSON.stringify({ crashed: String(e) })); process.exit(1); });
