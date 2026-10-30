/* Screenshots of the landing page, the product tour with each tab, a mid-scroll state, the report and the print view.
   Test tooling only: needs the app serving on http://127.0.0.1:8000 (uvicorn app.main:app, after `npm run build` in frontend/).
   Usage: node scripts/landing_screenshots.js [output folder, default docs/screenshots] [desktop|phone|both] */
const path = require("path");
const { chromium } = require("playwright");

const BASE = process.env.APP_URL || "http://127.0.0.1:8000";
const out = path.resolve(process.argv[2] || path.join(__dirname, "..", "docs", "screenshots"));
const which = process.argv[3] || "both";
const TABS = ["Dashboard", "Insights", "Report", "Data", "Cleaning log", "Plan"];

async function shootSet(browser, name, viewport, mobile) {
  const ctx = await browser.newContext({ viewport, isMobile: mobile, deviceScaleFactor: 1, colorScheme: "light" });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await page.waitForSelector(".tour-tabs", { timeout: 20000 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(out, `landing-top-${name}.png`) });

  await page.locator(".product-screen").scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
  const frame = async () => {
    const box = await page.locator(".product-screen").boundingBox();
    return { x: box.x, y: box.y, width: box.width, height: Math.min(box.height, 860 - box.y) };
  };
  for (const label of TABS) {
    await page.getByRole("tab", { name: label, exact: true }).click();
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(900);
    await page.screenshot({ path: path.join(out, `tour-${label.toLowerCase().replace(/\s+/g, "-")}-${name}.png`), clip: await frame() });
  }

  // mid-scroll: the third step is in view, so the screen should follow it (Analysis: Dashboard)
  await page.getByRole("tab", { name: "Dashboard", exact: true }).click();
  await page.waitForTimeout(600);
  const steps = await page.locator(".story-step").elementHandles();
  await steps[3].evaluate((el) => window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - window.innerHeight / 2 }));
  await page.waitForTimeout(900);
  await page.screenshot({ path: path.join(out, `tour-midscroll-${name}.png`) });

  await page.goto(`${BASE}/#/run/5a1e5d3e0001`, { waitUntil: "networkidle" });
  await page.locator("button.tab", { hasText: "Report" }).first().click();
  await page.waitForTimeout(700);
  await page.screenshot({ path: path.join(out, `report-${name}.png`), fullPage: false });
  await page.emulateMedia({ media: "print" });
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(out, `print-view-${name}.png`), fullPage: false });
  await page.emulateMedia({ media: "screen" });
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch();
  try {
    if (which !== "phone") await shootSet(browser, "desktop", { width: 1280, height: 860 }, false);
    if (which !== "desktop") await shootSet(browser, "phone", { width: 390, height: 844 }, true);
  } finally {
    await browser.close();
  }
  console.log("saved to", out);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
