/* Screenshots of the start page, the tour, the Try it yourself panel, how it works, the scroll story, the trust section, the report, the chat, the Insights tab,
   the print view, and the dark theme, at 1280x800 and 390x844.
   Test tooling only: needs the app serving on http://127.0.0.1:8000 (uvicorn app.main:app, after `npm run build` in frontend/).
   Usage: node scripts/landing_screenshots.js [output folder, default docs/screenshots] [desktop|phone|both] */
const path = require("path");
const { chromium } = require("playwright");

const BASE = process.env.APP_URL || "http://127.0.0.1:8000";
const out = path.resolve(process.argv[2] || path.join(__dirname, "..", "docs", "screenshots"));
const which = process.argv[3] || "both";
const TABS = ["Report", "Dashboard", "Insights", "Ideas to try", "Your data", "What we cleaned", "How we analysed"];
const slug = (s) => s.toLowerCase().replace(/\s+/g, "-");

async function settle(page, ms = 900) {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(ms);
}

async function set(browser, name, viewport, mobile, scheme) {
  const ctx = await browser.newContext({ viewport, isMobile: mobile, deviceScaleFactor: 1, colorScheme: scheme });
  const page = await ctx.newPage();
  const shot = (file, opts = {}) => page.screenshot({ path: path.join(out, `${file}-${name}${scheme === "dark" ? "-dark" : ""}.png`), ...opts });
  const clipOf = async (sel, pad = 0) => {
    const b = await page.locator(sel).first().boundingBox();
    return { x: Math.max(0, b.x - pad), y: Math.max(0, b.y - pad), width: Math.min(viewport.width, b.width + 2 * pad), height: Math.min(viewport.height - Math.max(0, b.y - pad), b.height + 2 * pad) };
  };

  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await page.addStyleTag({ content: "html { scroll-behavior: auto !important; }" });
  await page.waitForSelector(".tour-tabs .tour-tab");
  await settle(page, 1200);
  await shot("start-top");
  if (scheme === "dark") {
    await page.goto(`${BASE}/#/run/5a1e5d3e0001`, { waitUntil: "networkidle" });
    await page.waitForSelector(".report-page");
    await settle(page);
    await shot("report-top");
    await ctx.close();
    return;
  }

  // the tour, each tab
  await page.locator(".tour").scrollIntoViewIfNeeded();
  for (const label of TABS) {
    await page.getByRole("tab", { name: label, exact: true }).click();
    await settle(page, 1100);
    await page.locator(".tour").scrollIntoViewIfNeeded();
    await shot(`tour-${slug(label)}`, { clip: await clipOf(".tour", 4) });
  }

  // the Try it yourself panel
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.getByRole("button", { name: "Try it yourself" }).first().click();
  await page.waitForSelector(".dialog");
  await page.waitForTimeout(400);
  await shot("try-panel");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);

  // how it works, the story at two positions, the trust section
  await page.locator(".how").scrollIntoViewIfNeeded();
  await page.locator(".how").evaluate((el) => el.scrollIntoView({ block: "start" }));
  await settle(page, 1500);
  await shot("how-it-works");
  const steps = page.locator(".story-steps li");
  // scroll like a visitor: small wheel steps until the step is centred (the page shifts a little as the screen changes)
  const toStep = async (i) => {
    for (let k = 0; k < 40; k++) {
      const d = await steps.nth(i).evaluate((el) => { const r = el.getBoundingClientRect(); return r.top + r.height / 2 - window.innerHeight / 2; });
      if (Math.abs(d) < 30) break;
      await page.mouse.wheel(0, Math.max(-400, Math.min(400, d)));
      await page.waitForTimeout(120);
    }
  };
  for (const [i, tag] of [[0, "story-1"], [3, "story-4"]]) {
    await toStep(i);
    await settle(page, 1300);
    await shot(tag);
  }
  await page.locator(".trust").evaluate((el) => el.scrollIntoView({ block: "center" }));
  await settle(page, 500);
  await shot("trust");
  await page.locator(".closing").evaluate((el) => el.scrollIntoView({ block: "end" }));
  await settle(page, 400);
  await shot("closing");

  // the report, its charts, the chat
  await page.goto(`${BASE}/#/run/5a1e5d3e0001`, { waitUntil: "networkidle" });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForSelector(".report-page");
  await settle(page);
  await shot("report-top");
  await page.locator(".rp-chart").first().evaluate((el) => el.scrollIntoView({ block: "start" }));
  await settle(page, 500);
  await shot("report-charts");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.getByRole("button", { name: "Ask about this report" }).click();
  await page.waitForSelector(".chat-chips .chip");
  await page.locator(".chat-chips .chip").first().click();
  await page.waitForSelector(".msg.bot img, .msg.bot p");
  await settle(page, 1200);
  await shot("chat");
  await page.getByRole("button", { name: "Close the chat" }).click();

  // the Insights tab and the print view
  await page.locator("button.tab", { hasText: "Insights" }).first().click();
  await settle(page, 900);
  await shot("insights");
  await page.locator("button.tab", { hasText: "Report" }).first().click();
  await settle(page, 700);
  await page.emulateMedia({ media: "print" });
  await page.waitForTimeout(300);
  await shot("print-view");
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch();
  try {
    if (which !== "phone") {
      await set(browser, "desktop", { width: 1280, height: 800 }, false, "light");
      await set(browser, "desktop", { width: 1280, height: 800 }, false, "dark");
    }
    if (which !== "desktop") {
      await set(browser, "phone", { width: 390, height: 844 }, true, "light");
      await set(browser, "phone", { width: 390, height: 844 }, true, "dark");
    }
  } finally {
    await browser.close();
  }
  console.log("saved to", out);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
