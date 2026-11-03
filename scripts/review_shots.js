// One screenshot per tab (Report, Insights, Ideas to try, Dashboard, Data) at 1440 px wide, and the headings and chart cards as the browser shows them (dom.json).
// Used by scripts/review_run.py --screenshots. One browser, one page, one tab after another.
// Usage: NODE_PATH=$(npm root -g) node scripts/review_shots.js <base url> <run id> <out dir>
const { chromium } = require('playwright');
const fs = require('fs');
const [base, runId, out] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  const dom = {};
  try {
    await p.goto(`${base}/#/run/${runId}`);
    await p.waitForSelector('.report-page', { timeout: 90000 });
    await p.waitForTimeout(1200);
    for (const [file, name] of [['report', 'Report'], ['insights', 'Insights'], ['ideas-to-try', 'Ideas to try'], ['dashboard', 'Dashboard'], ['data', 'Data']]) {
      await p.getByRole('tab', { name: new RegExp('^' + name) }).first().click();
      await p.waitForTimeout(1500);
      await p.screenshot({ path: `${out}/${file}-1440.png`, fullPage: true });
      dom[name] = await p.evaluate(() => {
        const text = (e) => (e.innerText || '').replace(/\s+/g, ' ').trim();
        const headings = [...document.querySelectorAll('main h2, main h3, main h4, .report-page h2, .report-page h3')].map(text).filter(Boolean);
        const cards = [...document.querySelectorAll('.bi-card')].map((c) => {
          const h = c.querySelector('h4');
          const sub = h && h.querySelector('small');
          return { title: h ? text(h).replace(sub ? text(sub) : '', '').trim() : '', sub: sub ? text(sub) : '', kind: (c.className.match(/bi-card-(\w+)|kind-(\w+)/) || [])[0] || '' };
        });
        return { headings: [...new Set(headings)].slice(0, 60), cards };
      });
    }
    fs.writeFileSync(`${out}/dom.json`, JSON.stringify(dom, null, 1));
  } finally {
    await b.close();
  }
})().catch((e) => { console.error(e); process.exit(1); });
