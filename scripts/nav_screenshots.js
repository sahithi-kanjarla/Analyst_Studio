// Screenshots of the analysis view at 1280px and 1440px: Report, Insights, Ideas to try, Dashboard, each Data sub-view, Review answers open.
// Usage: NODE_PATH=$(npm root -g) node scripts/nav_screenshots.js <base url> <before|after> [out dir]
const { chromium } = require('playwright');
const base = process.argv[2], phase = process.argv[3] || 'after', out = process.argv[4] || 'docs/screenshots';
(async () => {
  const b = await chromium.launch();
  for (const w of [1280, 1440]) {
    const p = await b.newPage({ viewport: { width: w, height: 800 } });
    await p.goto(base); await p.evaluate(() => localStorage.clear());
    await p.getByRole('button', { name: /Try with sample data/i }).first().click(); await p.waitForSelector('.report-page'); await p.waitForTimeout(900);
    const shot = async (n) => { await p.waitForTimeout(500); await p.screenshot({ path: `${out}/nav-${phase}-${n}-${w}.png` }); };
    const tab = async (name) => { await p.getByRole(phase === 'after' ? 'tab' : 'button', { name: new RegExp('^' + name) }).first().click(); await p.waitForTimeout(700); };
    const sub = async (name) => { if (phase === 'before' && !(await p.getByRole('button', { name: new RegExp('^' + name) }).count())) return false; if (phase === 'after') { await p.getByRole('tab', { name: new RegExp('^' + name) }).first().click(); await p.waitForTimeout(600); } else await tab(name); };
    await shot('report');
    await tab('Insights'); await shot('insights');
    await tab('Ideas to try'); await shot('ideas-to-try');
    await tab('Dashboard'); await shot('dashboard');
    if (phase === 'after') await tab('Data'); else await tab('Your data');
    await shot('data-your-data');
    for (const [n, label] of [['how-files-connect', 'How files connect'], ['what-we-cleaned', 'What we cleaned'], ['how-we-analysed', 'How we analysed']]) { if (await sub(label) !== false) await shot('data-' + n); }
    await tab('Report');
    await p.getByRole('button', { name: /Review answers/ }).first().click(); await p.waitForTimeout(800); await shot('review-answers');
    console.log(w, 'overflow-x', await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth));
    await p.close();
  }
  await b.close();
})();
