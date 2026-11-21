// What on each GitHub tab page matches the completion gate's busy selector, sampled over 6 s.
process.env.PLAYWRIGHT_BROWSERS_PATH = "C:/Users/kiran/.conxa-build-studio-dev/sandbox/.conxa/chromium";
const { chromium } = require("C:/Users/kiran/Desktop/Conxa/runtime/node_modules/playwright");
(async () => {
  const b = await chromium.launch({ headless: true });
  const page = await b.newPage({ viewport: { width: 1280, height: 992 } });
  for (const u of ["", "/issues", "/pulls", "/actions", "/security", "/pulse"]) {
    await page.goto("https://github.com/cli/cli" + u, { waitUntil: "domcontentloaded" });
    const samples = [];
    for (let k = 0; k < 4; k++) {
      samples.push(await page.evaluate(() => {
        const sel = '[aria-busy="true"], [role="progressbar"], progress, .spinner, .loading, .loader, [class*="spinner" i]';
        return [...document.querySelectorAll(sel)].filter((el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none"; })
          .map((el) => `${el.tagName.toLowerCase()}[${el.getAttribute("role") || ""}] cls=${String(el.className).slice(0, 50)} now=${el.getAttribute("aria-valuenow") || ""} label=${(el.getAttribute("aria-label") || "").slice(0, 40)}`);
      }));
      await page.waitForTimeout(2000);
    }
    console.log(u || "/", JSON.stringify(samples), "| stable over 6s:", samples.every((s) => JSON.stringify(s) === JSON.stringify(samples[0])));
  }
  await b.close();
})();
