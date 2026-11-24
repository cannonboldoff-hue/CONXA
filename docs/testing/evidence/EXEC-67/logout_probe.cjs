// BUILD-117 (8) live probe: after signing in to the-internet's public demo, which `body > div.row` rows does the
// runtime's row matcher say hold "Logged in as"? Run with the old matcher (git HEAD) and the new one.
const path = require("path");
const RT = "C:/Users/kiran/Desktop/Conxa/runtime";
const { chromium } = require(path.join(RT, "node_modules", "playwright"));
const fresh = require(path.join(RT, "app", "page_scripts.js")).rowsHoldingWholeValue;
const old = new Function("return " + process.argv[2])();

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto("https://the-internet.herokuapp.com/login");
  await page.fill("#username", "tomsmith");
  await page.fill("#password", "SuperSecretPassword!");
  await page.click("button[type=submit]");
  await page.waitForURL(/\/secure/);
  const rows = page.locator("body > div.row");
  console.log("rows", await rows.count());
  console.log("old matcher", JSON.stringify(await rows.evaluateAll(old, "Logged in as")));
  console.log("new matcher", JSON.stringify(await rows.evaluateAll(fresh, "Logged in as")));
  console.log("new matcher, part of the run", JSON.stringify(await rows.evaluateAll(fresh, "Logged in")));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
