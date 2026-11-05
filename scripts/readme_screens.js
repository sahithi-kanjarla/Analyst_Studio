// Takes the three README screenshots of the shipped sample run (no AI). Usage: NODE_PATH=<node_modules> node scripts/readme_screens.js http://localhost:8000
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://localhost:8000';
const RUN = '5a1e5d3e0001';
(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  const tab = async (n) => { await p.locator('.runbar-tabs').getByRole('tab', { name: new RegExp('^' + n) }).click(); await p.waitForTimeout(1500); };
  await p.goto(`${base}/#/run/${RUN}`); await p.reload(); await p.waitForSelector('.runbar', { timeout: 15000 }); await p.waitForTimeout(2000);
  await p.screenshot({ path: 'docs/screens/report.png' });
  await tab('Dashboard'); await p.waitForFunction(() => !document.body.innerText.includes('Building your dashboard'), null, { timeout: 90000 }); await p.waitForTimeout(2500); await p.screenshot({ path: 'docs/screens/dashboard.png' });
  await tab('Data');
  await p.getByRole('tablist', { name: 'Data views' }).getByRole('tab', { name: 'How files connect' }).click(); await p.waitForTimeout(1500);
  await p.screenshot({ path: 'docs/screens/how-files-connect.png' });
  await b.close();
})();
