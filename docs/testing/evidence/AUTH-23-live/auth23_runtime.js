// AUTH-23: does the runtime's own pre-flight (browser.js::_isAuthenticated, the real code) accept the learned
// definition on the live site with the saved session, and reject it with no session?
// Usage: node auth23_runtime.js <result.json>   (reads <result>.state.json beside it)
const path = require("path");
const fs = require("fs");
const RT = path.resolve(__dirname, "../../../../runtime");
const { chromium } = require(path.join(RT, "node_modules/playwright"));
const browserMod = require(path.join(RT, "app/browser.js"));

(async () => {
  const resultPath = path.resolve(process.argv[2]);
  const result = JSON.parse(fs.readFileSync(resultPath, "utf8"));
  const statePath = resultPath.replace(/\.json$/, ".state.json");
  const app = { auth_definition: result.definition, success_url: result.success_url };
  const browser = await chromium.launch({ headless: true });
  const out = {};
  try {
    for (const [label, opts] of [["with_saved_session", { storageState: statePath }], ["no_session", {}]]) {
      const ctx = await browser.newContext(opts);
      const page = await ctx.newPage();
      out[label] = await browserMod._isAuthenticated({ browser, hostOwned: false }, page, result.login_url, app);
      await ctx.close();
    }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify({ site: result.site, runtime_isAuthenticated: out }));
  process.exit(0);
})();
