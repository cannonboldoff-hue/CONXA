// Drive Build Studio over CDP. Usage: node studio_driver.mjs <cdpPort> <command> [args]
// Playwright comes from runtime/node_modules (no new deps). Prints compact JSON.
// Locators are text/role based (renderer has almost no test ids). VERIFIED = run against live Studio; others UNVERIFIED.
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, "../../../runtime/package.json"))("playwright");

const [port, cmd, ...args] = process.argv.slice(2);
const out = (o) => console.log(JSON.stringify(o));
if (!port || !cmd) { out({ error: "usage: studio_driver.mjs <cdpPort> <pages|open-workflow|text|record|answer|compile|test|result> [args]" }); process.exit(2); }

const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
const pages = browser.contexts().flatMap((c) => c.pages());
// Studio window = the renderer page (vite dev http://localhost:517x or packaged app://); browser-panel tabs are other pages.
const studio = pages.find((p) => /localhost:517\d|^app:|^file:/.test(p.url())) ?? pages[0];
const MAX = 6000;
const text = async () => (await studio.innerText("body")).replace(/\n{2,}/g, "\n").slice(0, MAX);
const click = async (loc, what) => { try { await loc.first().click({ timeout: 8000 }); return { ok: true, clicked: what }; } catch (e) { return { ok: false, error: `${what} not found/clickable`, hint: String(e.message).split("\n")[0] }; } };

try {
  switch (cmd) {
    case "pages": // VERIFIED
      out(pages.map((p) => p.url()));
      break;
    case "text": // VERIFIED
      out({ text: await text() });
      break;
    case "open-workflow": { // UNVERIFIED: sidebar search box + row text
      const name = args.join(" ");
      const box = studio.getByPlaceholder("Search workflows and groups");
      if (await box.count()) await box.fill(name);
      out(await click(studio.getByText(name, { exact: false }), `workflow "${name}"`));
      break;
    }
    case "record": { // UNVERIFIED: opens "Record Workflow" dialog (RecordWorkflowDialog.tsx); URL field placeholder https://...
      const url = args[0];
      let r = await click(studio.getByRole("button", { name: /record (a )?workflow|record it now|record again/i }), "Record button");
      if (!r.ok) { out(r); break; }
      const f = studio.getByPlaceholder(/^https?:\/\//).first();
      try { await f.fill(url, { timeout: 5000 }); } catch { /* dialog may start from workflow URL */ }
      out({ ok: true, note: "recording dialog opened; finish via browser panel / Finish recording (UNVERIFIED)", url });
      break;
    }
    case "answer": { // UNVERIFIED: click a choice button by text, else type into chat box (placeholder from ChatPage/Conversation)
      const a = args.join(" ");
      const choice = studio.getByRole("button", { name: a, exact: true });
      if (await choice.count()) { out(await click(choice, `choice "${a}"`)); break; }
      const box = studio.getByPlaceholder("Ask about this workflow, or describe a change");
      await box.fill(a); await studio.getByRole("button", { name: "Send" }).click();
      out({ ok: true, sent: a });
      break;
    }
    case "compile": // UNVERIFIED: CompileClarifyCards "Compile now"
      out(await click(studio.getByRole("button", { name: /compile now/i }), "Compile now"));
      break;
    case "test": // UNVERIFIED: CompileClarifyCards "Test it" / "Test again"
      out(await click(studio.getByRole("button", { name: /^test (it|again)$/i }), "Test it/Test again"));
      break;
    case "result": { // UNVERIFIED: heuristic scan for pass/fail wording
      const t = await text();
      const m = t.match(/Test passed|Test failed|didn.t finish|Publishing failed|Compile report/gi);
      out({ markers: m ?? [], text: t.slice(-2000) });
      break;
    }
    case "click": // VERIFIED-ish: click by visible text (regex ok via /re/)
      out(await click(studio.getByText(args[0].startsWith("/") ? new RegExp(args[0].slice(1,-1),"i") : args.join(" ")), args.join(" ")));
      break;
    case "type": // type <placeholderRegex> <text>
      await studio.getByPlaceholder(new RegExp(args[0],"i")).first().fill(args.slice(1).join(" ")); out({ ok: true });
      break;
    case "press": await studio.keyboard.press(args[0]); out({ ok: true }); break;
    case "tail": { const t=(await studio.innerText("body")).replace(/\n{2,}/g,"\n"); out({ text: t.slice(-(+args[0]||3000)) }); break; }
    case "btns": out((await studio.getByRole("button").allInnerTexts()).filter(Boolean).slice(-40)); break;
    case "eval": out(await studio.evaluate(args.join(" "))); break;
    case "ext": { const p = pages.find((x)=>!/localhost:5174/.test(x.url())); out({ urls: pages.map(x=>x.url()) }); if (args[0]) out(await (p??studio).evaluate(args.join(" "))); break; }    case "osclick": { // osclick <cssSelector> : TRUSTED OS click on the recording page element (recorder ignores CDP/JS input). Calibrated: phys = 700+(x-68)/k, 500+(y-395)/k, k=pane scale (page px per screen px) measured via osinput.ps1 move.
      const p = pages.find((x) => !/localhost:5174/.test(x.url()));
      const k = Number(process.env.OSK || 1.13);
      const r = await p.evaluate((sel) => { const b = document.querySelector(sel).getBoundingClientRect(); return [b.x + b.width / 2, b.y + b.height / 2]; }, args[0]);
      const px = Math.round(700 + (r[0] - 68) / k), py = Math.round(500 + (r[1] - 395) / k);
      const { execFileSync } = await import("node:child_process");
      const ps = path.join(here, "osinput.ps1");
      execFileSync("powershell", ["-NoProfile", "-File", ps, "focus"]); await new Promise((z) => setTimeout(z, 400));
      execFileSync("powershell", ["-NoProfile", "-File", ps, "click", String(px), String(py)]);
      if (args[1]) { await new Promise((z) => setTimeout(z, 300)); execFileSync("powershell", ["-NoProfile", "-File", ps, "type", args.slice(1).join(" ")]); }
      out({ ok: true, at: [px, py] });
      break;
    }
    case "act": { // act '<json array>' on the recording page (last non-Studio page). Steps: {goto},{click},{fill:[sel,val]},{wait:ms},{text:sel},{press}
      const p = pages.filter((x)=>x!==studio).pop(); const res = [];
      for (const s of JSON.parse(args.join(" "))) {
        try {
          if (s.goto) await p.goto(s.goto);
          else if (s.click) await p.click(s.click, { timeout: 8000 });
          else if (s.fill) await p.fill(s.fill[0], s.fill[1]);
          else if (s.js) res.push(JSON.stringify(await p.evaluate(s.js)).slice(0,500));
          else if (s.force) await p.click(s.force, { force: true, timeout: 5000 });
          else if (s.mouse) await p.mouse.click(s.mouse[0], s.mouse[1]);
          else if (s.cdpclick) { const cs = await p.context().newCDPSession(p); const [x,y]=s.cdpclick; for (const t of ["mouseMoved","mousePressed","mouseReleased"]) await cs.send("Input.dispatchMouseEvent",{type:t,x,y,button:"left",clickCount:1}); }
          else if (s.clearcookies) await p.context().clearCookies();
          else if (s.wait) await p.waitForTimeout(s.wait);
          else if (s.press) await p.keyboard.press(s.press);
          else if (s.text) res.push((await p.innerText(s.text)).slice(0, 400));
          res.push("ok");
        } catch (e) { res.push("ERR " + String(e.message).split("\n")[0]); }
      }
      out({ url: p.url(), res }); break;
    }    default: out({ error: `unknown command ${cmd}` });
  }
} finally { await browser.close(); }




