"use strict";
// Evidence notebook (Proof of Done stage 3): what every tab and visible frame of a run shows, plus
// request/response METADATA. Nothing here may keep a value: no query string, id, header or reply body.
const test = require("node:test");
const assert = require("node:assert");
const { EventEmitter } = require("node:events");

const pageScripts = require("../../app/page_scripts");
const evidence = require("../../app/evidence");
const { routeTemplate } = require("../../app/route_template");
const { waitForCompletion } = require("../../app/completion");

const CLEAN = { busyCount: 0, regions: [] };
const TRAY = { busyCount: 0, regions: ["Uploading 1 item"] };
const DONE = { busyCount: 0, regions: ["1 upload complete"] };

// A page with a real event emitter and scripted probe answers (the last one repeats).
function fakePage({ url = "https://drive.test/app", samples = [CLEAN], frames = [] } = {}) {
  const page = new EventEmitter();
  let calls = 0;
  const main = { name: "main" };
  page.url = () => url;
  page.isClosed = () => false;
  page.mainFrame = () => main;
  page.frames = () => [main, ...frames];
  page.evaluate = async (script) => {
    if (script !== pageScripts.completionProbe) return null;
    return samples[Math.min(calls++, samples.length - 1)];
  };
  return page;
}
// A child frame: its own scripted answers, and whether the <iframe> element is on screen.
function fakeFrame(samples, { visible = true } = {}) {
  let calls = 0;
  return {
    evaluate: async (script) => (script === pageScripts.completionProbe ? samples[Math.min(calls++, samples.length - 1)] : null),
    frameElement: async () => ({ isVisible: async () => visible }),
  };
}

function req(method, url, type = "xhr") {
  return { method: () => method, url: () => url, resourceType: () => type, isNavigationRequest: () => false, failure: () => ({ errorText: "net::ERR_CONNECTION_RESET" }) };
}
function resp(request, status, { body = null, type = "application/json", length } = {}) {
  const buf = body == null ? null : Buffer.from(typeof body === "string" ? body : JSON.stringify(body));
  return {
    status: () => status,
    request: () => request,
    headers: () => ({ "content-type": type, ...(length != null ? { "content-length": String(length) } : {}) }),
    body: async () => buf,
  };
}
const settle = () => new Promise((r) => setTimeout(r, 15));
const opts = (nb, over) => ({ baseline: nb.baseline, sinceMs: 0, mode: "full", budgetMs: 1500, pollMs: 10, quietMs: 30, startGraceMs: 120, notebook: nb, ...over });

test("routeTemplate keeps the structure and drops every per-run value", () => {
  assert.strictEqual(routeTemplate("https://drive.test/upload/abc123?token=secret&name=report.pdf#frag"), "https://drive.test/upload/*");
  assert.strictEqual(routeTemplate("https://drive.test/files/v2/list"), "https://drive.test/files/*/list", "a segment with a digit is a value");
  assert.strictEqual(routeTemplate("https://drive.test/"), "https://drive.test");
  assert.strictEqual(routeTemplate("https://x.test/api/ab12cd34ef56gh78ij90"), "https://x.test/api/*", "a long token is a value");
  assert.strictEqual(routeTemplate("not a url"), "");
  assert.strictEqual(routeTemplate("file:///etc/passwd"), "");
});

test("bodyShape keeps key names only, and flags a reply that says the action failed", () => {
  const { bodyShape } = evidence;
  assert.deepStrictEqual(bodyShape({ id: "x1", name: "report.pdf" }), { top_keys: ["id", "name"], error_flag: false });
  assert.strictEqual(bodyShape({ error: { code: "storageQuotaExceeded" } }).error_flag, true);
  assert.strictEqual(bodyShape({ errors: [] }).error_flag, false, "an empty errors list is not an error");
  assert.strictEqual(bodyShape({ error: null, id: 1 }).error_flag, false);
  assert.strictEqual(bodyShape({ success: false }).error_flag, true);
  assert.strictEqual(bodyShape({ status: "failed" }).error_flag, true);
  assert.strictEqual(bodyShape({ status: "ok" }).error_flag, false);
  assert.strictEqual(bodyShape({ result: { error: "quota" } }).error_flag, true, "one level down counts");
  assert.strictEqual(bodyShape({ data: { id: 1 }, errors: [{ message: "a field was slow" }] }).error_flag, false, "GraphQL: usable data next to errors is a partial answer");
  assert.strictEqual(bodyShape({ data: null, errors: [{ message: "denied" }] }).error_flag, true);
  assert.strictEqual(bodyShape([1, 2]), null);
  assert.deepStrictEqual(bodyShape({ "user@example.com": 1, ok: 1 }).top_keys, ["ok"], "a key that is not identifier-like is data, not schema");
});

test("progress inside an iframe is noticed, and the gate waits for it to clear", async () => {
  const frame = fakeFrame([TRAY, TRAY, TRAY, DONE]);
  const page = fakePage({ frames: [frame], samples: [CLEAN] });
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  const v = await waitForCompletion(page, opts(nb));
  assert.strictEqual(v.status, "done");
  assert.ok(v.waitedMs >= 30, `did not wait for the iframe tray: ${v.waitedMs}ms`);
  nb.close();
});

test("a frame that is not on screen says nothing about what the person sees", async () => {
  const hidden = fakeFrame([TRAY], { visible: false });
  const page = fakePage({ frames: [hidden] });
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  const s = await nb.sample();
  assert.deepStrictEqual(s, { busyCount: 0, regions: [] });
  nb.close();
});

test("a refusal in a second tab fails the step", async () => {
  const a = fakePage();
  const b = fakePage({ url: "https://drive.test/popup" });
  const nb = await evidence.open({ page: a, pagesOf: () => [a, b] });
  const r = req("POST", "https://drive.test/upload/abc123?x=1");
  b.emit("request", r);
  b.emit("response", resp(r, 413));
  b.emit("requestfinished", r);
  await settle();
  const v = await waitForCompletion(a, opts(nb));
  assert.strictEqual(v.status, "failed");
  assert.match(v.evidence, /status 413/);
  nb.close();
});

test("another run's page is not this run's evidence", async () => {
  const mine = fakePage();
  const theirs = fakePage({ url: "https://other.test/" });
  const nb = await evidence.open({ page: mine, pagesOf: () => [mine] }); // theirs is not named
  const r = req("POST", "https://other.test/save");
  theirs.emit("request", r);
  theirs.emit("response", resp(r, 500));
  await settle();
  assert.strictEqual(nb.mutationFailure(), null);
  assert.strictEqual(nb.mutationStarted(), false);
  nb.close();
});

test("a 200 whose reply says the action failed (storage full) fails the step", async () => {
  const page = fakePage();
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  const r = req("POST", "https://drive.test/upload/abc123");
  page.emit("request", r);
  page.emit("response", resp(r, 200, { body: { error: { code: "storageQuotaExceeded", message: "Your storage is full" } } }));
  page.emit("requestfinished", r);
  await settle();
  const v = await waitForCompletion(page, opts(nb));
  assert.strictEqual(v.status, "failed");
  assert.match(v.evidence, /status 200, but the reply says the action failed/);
  nb.close();
});

test("a clean 200 is a first sign and a pass; a later success on the same endpoint clears an earlier refusal", async () => {
  const page = fakePage();
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  const r1 = req("POST", "https://drive.test/upload/aaa111");
  page.emit("request", r1);
  page.emit("response", resp(r1, 500));
  page.emit("requestfinished", r1);
  const r2 = req("POST", "https://drive.test/upload/bbb222");
  page.emit("request", r2);
  page.emit("response", resp(r2, 200, { body: { id: "f1" } }));
  page.emit("requestfinished", r2);
  await settle();
  assert.strictEqual(nb.mutationFailure(), null, "same endpoint template, so the retry cleared the refusal");
  const v = await waitForCompletion(page, opts(nb));
  assert.strictEqual(v.status, "done");
  nb.close();
});

test("an open data-changing request holds the wait until it finishes", async () => {
  const page = fakePage();
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  const r = req("PUT", "https://drive.test/upload/zzz999");
  page.emit("request", r);
  setTimeout(() => page.emit("requestfinished", r), 120);
  const v = await waitForCompletion(page, opts(nb, { budgetMs: 2000 }));
  assert.strictEqual(v.status, "done");
  assert.ok(v.waitedMs >= 100, `finished early: ${v.waitedMs}ms`);
  nb.close();
});

test("a new tab and a download are first signs the app took the work", async () => {
  const a = fakePage();
  const pages = [a];
  const nb = await evidence.open({ page: a, pagesOf: () => pages });
  assert.strictEqual(nb.sawStart(), false);
  const b = fakePage({ url: "https://drive.test/new" });
  pages.push(b);
  await nb.sample(); // refresh() picks the new tab up
  assert.strictEqual(nb.newPages(), 1);
  assert.strictEqual(nb.sawStart(), true);
  nb.close();

  const c = fakePage();
  const nb2 = await evidence.open({ page: c, pagesOf: () => [c] });
  c.emit("download", {});
  assert.deepStrictEqual([nb2.downloads(), nb2.sawStart()], [1, true]);
  nb2.close();
});

test("an address change on any run tab is a first sign", async () => {
  let url = "https://drive.test/app";
  const page = fakePage();
  page.url = () => url;
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  assert.strictEqual(nb.sawStart(), false);
  url = "https://drive.test/app/done";
  assert.strictEqual(nb.sawStart(), true);
  nb.close();
});

test("PRIVACY: the notebook keeps no query string, id, header or reply value", async () => {
  const page = fakePage();
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  const r = req("POST", "https://drive.test/upload/abc123?token=SECRET-TOKEN&name=quarterly-report.pdf");
  page.emit("request", r);
  page.emit("response", resp(r, 200, {
    body: { id: "file-id-777", owner: "alice@example.com", title: "Quarterly report", error: { message: "STORAGE-FULL-MESSAGE" } },
    length: 321,
  }));
  page.emit("requestfinished", r);
  await settle();
  const dump = JSON.stringify([nb.entries(), nb.summary(), nb.mutationFailure()]);
  for (const secret of ["SECRET-TOKEN", "quarterly-report", "abc123", "file-id-777", "alice@example.com", "Quarterly report", "STORAGE-FULL-MESSAGE", "content-type"]) {
    assert.ok(!dump.includes(secret), `the notebook kept "${secret}"`);
  }
  const e = nb.entries()[0];
  assert.deepStrictEqual([e.m, e.t, e.s, e.sz], ["POST", "https://drive.test/upload/*", 200, 321]);
  assert.deepStrictEqual(e.shape.top_keys, ["id", "owner", "title", "error"]);
  nb.close();
});

test("a big or non-JSON reply is never read", async () => {
  const page = fakePage();
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  let read = 0;
  const big = req("POST", "https://drive.test/a/b1");
  page.emit("request", big);
  const bigResp = resp(big, 200, { body: { error: "x" }, length: 10 * 1024 * 1024 });
  const origBody = bigResp.body; bigResp.body = async () => { read++; return origBody(); };
  page.emit("response", bigResp);
  const html = req("POST", "https://drive.test/a/b2");
  page.emit("request", html);
  const htmlResp = resp(html, 200, { body: "<html>", type: "text/html" });
  const origHtml = htmlResp.body; htmlResp.body = async () => { read++; return origHtml(); };
  page.emit("response", htmlResp);
  await settle();
  assert.strictEqual(read, 0);
  assert.strictEqual(nb.mutationFailure(), null);
  nb.close();
});

test("close() removes every listener, and entries are capped", async () => {
  const page = fakePage();
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  for (let i = 0; i < evidence.MAX_ENTRIES + 50; i++) {
    const r = req("GET", `https://drive.test/poll/${i}`, "fetch");
    page.emit("request", r);
    page.emit("requestfinished", r);
  }
  assert.strictEqual(nb.entries().length, evidence.MAX_ENTRIES);
  nb.close();
  for (const ev of ["request", "response", "requestfinished", "requestfailed", "download"]) {
    assert.strictEqual(page.listenerCount(ev), 0, `${ev} listener left behind`);
  }
});

test("a step page that cannot answer reads as 'cannot tell', not as quiet", async () => {
  const page = fakePage();
  page.evaluate = async () => { throw new Error("closed"); };
  const nb = await evidence.open({ page, pagesOf: () => [page] });
  assert.strictEqual(nb.baseline, null);
  assert.strictEqual(await nb.sample(), null);
  const v = await waitForCompletion(page, opts(nb, { baseline: CLEAN }));
  assert.strictEqual(v.status, "cant_tell");
  nb.close();
});
