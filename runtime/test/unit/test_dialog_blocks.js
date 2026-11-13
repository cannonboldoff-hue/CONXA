"use strict";
// dialogProbe().blocks is MEASURED on a real page: can the person (or the run) still reach the rest of the page?
// true = modal / covering backdrop / inert page; false = the rest of the page is reachable; null = could not tell.
// Words never enter into it. Real headless Chromium, because hit-testing is what is being tested.

const test = require("node:test");
const assert = require("node:assert");
const { chromium } = require("playwright");
const pageScripts = require("../../app/page_scripts");

const PAGE = (inner) => `<!doctype html><html><body style="margin:0;font:14px sans-serif">
  <main style="padding:20px"><h1>Files</h1><p>${"Some of the page's own content. ".repeat(40)}</p><button>Real button</button></main>${inner}</body></html>`;

const CASES = {
  // Drive's upload tray: a small non-modal dialog in a corner.
  "a corner tray leaves the page usable": [
    `<div role="dialog" style="position:fixed;right:16px;bottom:16px;width:300px;height:90px;background:#fff">
       <b>1 upload complete</b><button>Minimize</button><button>Close</button><button>Show file location</button></div>`, false],
  "a dialog over a full-window backdrop that is its sibling blocks": [
    `<div style="position:fixed;inset:0;background:rgba(0,0,0,.5)"></div>
     <div role="dialog" style="position:fixed;left:30%;top:30%;width:40%;height:30%;background:#fff"><b>Replace?</b><button>Replace</button><button>Keep</button></div>`, true],
  "a dialog inside a full-window overlay wrapper (no aria-modal) blocks": [
    `<div style="position:fixed;inset:0;background:rgba(0,0,0,.5)">
       <div role="dialog" style="margin:20vh auto;width:40%;background:#fff"><b>Replace?</b><button>Replace</button><button>Keep</button></div></div>`, true],
  "aria-modal blocks": [
    `<div role="dialog" aria-modal="true" style="position:fixed;right:16px;bottom:16px;width:300px;height:90px;background:#fff">
       <b>Sure?</b><button>Yes</button><button>No</button></div>`, true],
  "a dialog inside a window-sized app shell that holds the page itself does not block": [
    `<div id="shell" style="position:fixed;inset:0;overflow:auto"><p>${"The whole application lives in this shell. ".repeat(60)}</p>
       <div role="dialog" style="position:fixed;right:16px;bottom:16px;width:300px;height:90px;background:#fff"><b>Saved</b><button>Close</button><button>Undo</button></div></div>`, false],
};

test("dialogProbe.blocks is measured from the page, not read from words", async (t) => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [name, [html, want]] of Object.entries(CASES)) {
      const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
      await page.setContent(PAGE(html));
      const probe = await page.evaluate(pageScripts.dialogProbe);
      assert.ok(probe, name);
      assert.strictEqual(probe.blocks, want, name);
      await page.close();
    }
  } finally {
    await browser.close();
  }
});
