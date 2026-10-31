/* Screenshots of Lens on the sample report, at 1280x800 and 390x844: the round button, the docked panel (bottom sheet on a phone), an answer with its chart and
   "How this was calculated", the chart controls, the pinned section, and the full-width Chat tab. Also drives the same flow and fails loudly if a step is missing.
   Test tooling only: needs the app serving on http://127.0.0.1:8000 (uvicorn app.main:app, after `npm run build` in frontend/) and Playwright.
   Usage: node scripts/lens_screenshots.js [output folder, default docs/screenshots]   (CHROME_PATH=<chrome.exe> to use an installed Chromium) */
const path = require("path");
const { chromium } = require("playwright");

const BASE = process.env.APP_URL || "http://127.0.0.1:8000";
const out = path.resolve(process.argv[2] || path.join(__dirname, "..", "docs", "screenshots"));

async function flow(browser, name, viewport, mobile) {
  const ctx = await browser.newContext({ viewport, isMobile: mobile, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const shot = (file, opts = {}) => page.screenshot({ path: path.join(out, `lens-${file}-${name}.png`), ...opts });
  const demo = await (await ctx.request.post(`${BASE}/api/demo`)).json();
  await page.goto(`${BASE}/#/run/${demo.run_id}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".tabs .tab");
  await page.addStyleTag({ content: "html { scroll-behavior: auto !important; }" });

  await page.waitForSelector(".lens-fab");
  await shot("button");
  await page.locator(".lens-fab").click();
  await page.waitForSelector(".lens-panel .lens-chips .chip");
  await shot("panel");

  const turns = () => page.locator(".lens-panel [data-turn]").count();
  const before = await turns();                                                   // a run keeps its conversation, so count from where it is
  await page.locator(".lens-panel .lens-chips .chip").first().click();            // the first suggested question
  await page.waitForFunction((n) => document.querySelectorAll(".lens-panel [data-turn]").length > n && !document.querySelector(".lens-panel .typing"), before);
  await page.locator(".lens-panel .lens-details summary", { hasText: "How this was calculated" }).nth(before).click();
  await shot("answer");

  await page.locator(".lens-panel .lens-next .chip").nth(0).click();
  await page.waitForFunction((n) => document.querySelectorAll(".lens-panel [data-turn]").length > n + 1 && !document.querySelector(".lens-panel .typing"), before);
  await page.locator(".lens-panel .lens-details summary", { hasText: "Change the chart" }).last().click();
  await shot("controls");

  await page.locator(".lens-panel .lens-tools button", { hasText: "Pin to report" }).last().click();
  await page.locator(".lens-panel .lens-tools button", { hasText: "Pin to dashboard" }).last().click();
  await page.waitForSelector(".lens-panel .lens-tools button[aria-pressed=true]");

  await page.locator(".lens-panel .lens-pic").last().click();                     // Expand the chart
  await page.waitForSelector(".dialog .lens-big");
  await shot("expanded-chart");
  await page.keyboard.press("Escape");

  if (!mobile) {
    await page.getByRole("button", { name: "Expand", exact: true }).first().click();      // the same conversation as a full-width tab
    await page.waitForSelector(".lens-chat-tab");
    await shot("chat-tab");
    await page.locator(".lens-fab").click();                                         // back to the side panel
    await page.waitForSelector(".lens-panel");
    await page.locator(".lens-panel").getByRole("button", { name: "Close Lens" }).click();
  } else {
    await page.locator(".lens-panel").getByRole("button", { name: "Close Lens" }).click();
  }
  await page.getByRole("button", { name: "Report", exact: true }).first().click();
  await page.waitForSelector(".lens-pins");
  await page.locator(".lens-pins").scrollIntoViewIfNeeded();
  await shot("pinned-report");
  await page.getByRole("button", { name: "Dashboard", exact: true }).first().click();
  await page.waitForSelector(".lens-pins");
  await page.locator(".lens-pins").scrollIntoViewIfNeeded();
  await shot("pinned-dashboard");
  await page.locator(".lens-pins .link", { hasText: "Remove from dashboard" }).first().click();
  await page.waitForSelector(".lens-pins", { state: "detached" });
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
  await flow(browser, "desktop", { width: 1280, height: 800 }, false);
  await flow(browser, "phone", { width: 390, height: 844 }, true);
  await browser.close();
  console.log("ok: screenshots in", out);
})().catch((e) => { console.error(e); process.exit(1); });
