// WF-19 test hook, loaded into Execute's main process via NODE_OPTIONS=--require: shell.openExternal never opens the owner's real browser.
// A mock-sign-in URL is followed like a person who approves (so the app's own callback server gets its own redirect); anything else is only logged.
// (require('electron') is not resolvable this early, so it is retried until Electron's loader is up.)
const fs = require("fs");
const LOG = process.env.WF19_OPENEXT_LOG || "wf19-openext.log";
const log = (s) => fs.appendFileSync(LOG, `${new Date().toISOString()} ${s}\n`);
let tries = 0;
const t = setInterval(() => {
  let shell;
  try { shell = require("electron").shell; } catch (e) { if (++tries > 4000) { clearInterval(t); log("hook failed " + e); } return; }
  if (!shell) return;
  clearInterval(t);
  shell.openExternal = async (url) => {
    log(`openExternal ${String(url).slice(0, 200)}`);
    if (/^http:\/\/127\.0\.0\.1:8110\//.test(url)) {
      try { const r = await fetch(url, { redirect: "follow" }); log(`  followed -> ${r.status}`); } catch (e) { log(`  follow failed ${e}`); }
    } else log("  NOT opened (test hook)");
  };
  log("hook installed");
}, 5);
