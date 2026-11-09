"use strict";
/**
 * evidence.js — the evidence notebook (Proof of Done, stage 3).
 *
 * Opened just BEFORE a step's action and closed once its verdict is in. While it is open it writes down
 * what happens next, on every tab this run owns and in every visible frame, so the facts the completion
 * check needs are not limited to the one page and the one document the step happened to run on:
 *
 *   - what the screen shows: busy indicators and status/progress text, from every visible frame
 *   - request and response METADATA (method, a scrubbed route template, status, kind, size)
 *   - data-changing requests that started, are still open, were refused, or answered "OK" with a body that
 *     says the action failed (the body is read once, reduced to {top_keys, error_flag}, and discarded)
 *   - downloads, new tabs, address changes
 *
 * It only observes. It never clicks, types, answers a popup or loads a page. Page contents stay on this
 * computer: an entry holds a route template (ids, tokens and the query string removed), never a URL with
 * values, never a header, never a body. Nothing here is sent to the cloud.
 *
 * "This run's own pages" is deliberate: when the runtime borrows a browser from Conxa Execute or Build
 * Studio, the browser context also holds other runs' views, so listening on the context would fail this
 * run for another run's refusal (see RT-10). The caller names the pages (`pagesOf`).
 */
const pageScripts = require("./page_scripts");
const { evalOn, EVAL_TIMED_OUT } = require("./page_eval");
const { routeTemplate } = require("./route_template");

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const MUTATION_REFUSED = new Set([401, 403, 409, 413, 422, 429]);
const MAX_ENTRIES = 300;
const BODY_PEEK_MAX_BYTES = 64 * 1024;
const BODY_PEEK_TIMEOUT_MS = 1500;
const MAX_FRAMES_PER_PAGE = 12;
const MAX_REGIONS = 24;

const FAIL_KEYS = new Set(["error", "errors", "err", "exception", "exceptions", "failure", "failures"]);
const FAIL_WORDS = new Set(["error", "failed", "failure", "fail", "rejected"]);
const KEY_RE = /^[A-Za-z_][A-Za-z0-9_]{0,31}$/;

const isEmptyish = (v) => v == null || v === false || v === 0 || v === ""
  || (Array.isArray(v) && v.length === 0)
  || (typeof v === "object" && !Array.isArray(v) && Object.keys(v).length === 0);

function errorReasons(obj) {
  const reasons = new Set();
  for (const k of Object.keys(obj)) {
    const lk = k.toLowerCase();
    const v = obj[k];
    if (FAIL_KEYS.has(lk) && !isEmptyish(v)) reasons.add(lk);
    else if ((lk === "success" || lk === "ok") && v === false) reasons.add(lk);
    else if ((lk === "status" || lk === "state" || lk === "result") && typeof v === "string" && FAIL_WORDS.has(v.toLowerCase())) reasons.add(lk);
  }
  return reasons;
}

/**
 * Pure. What a JSON reply looks like, WITHOUT its values: the names of its top-level keys (identifier-like
 * ones only, at most 12) and whether it says the action failed. A GraphQL reply with errors next to usable
 * data is a partial answer, not a failed action. Null for anything that is not a JSON object.
 */
function bodyShape(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const top_keys = Object.keys(value).filter((k) => KEY_RE.test(k)).slice(0, 12);
  let reasons = errorReasons(value);
  for (const nested of ["data", "result", "response"]) {
    const child = value[nested];
    if (child && typeof child === "object" && !Array.isArray(child)) for (const r of errorReasons(child)) reasons.add(r);
  }
  const data = value.data;
  const usableData = data && typeof data === "object" && Object.keys(data).length > 0 && errorReasons(data).size === 0;
  if (reasons.size === 1 && reasons.has("errors") && usableData) reasons = new Set();
  return { top_keys, error_flag: reasons.size > 0 };
}

function endpointKey(method, template) { return `${method} ${template}`; }

/**
 * @param {{pagesOf: () => any[], page: any, sinceMs?: number, baseline?: {busyCount:number, regions:string[]}}} o
 *   pagesOf: this run's own pages (never the whole browser context). page: the step's page.
 * @returns {Promise<Notebook>}
 */
async function open(o) {
  const sinceMs = o.sinceMs ?? Date.now();
  const watched = new Map();   // page -> { detach() }
  const urlAtOpen = new Map(); // page -> url when the notebook opened
  const entries = [];          // metadata only, newest last, capped
  const openReqs = new Map();  // request -> entry
  const state = { downloads: 0, newPages: 0, started: false, failed: null, mutationStartedAt: 0, closed: false };
  const initial = new Set();
  const seenNew = new Set();   // every status/progress text that was NOT on screen when the notebook opened

  const safeUrl = (p) => { try { return typeof p.url === "function" ? p.url() : null; } catch (_) { return null; } };
  const isClosed = (p) => { try { return typeof p.isClosed === "function" && p.isClosed(); } catch (_) { return false; } };
  const livePages = () => {
    let list = [];
    try { list = (o.pagesOf && o.pagesOf()) || []; } catch (_) { list = []; }
    if (o.page && !list.includes(o.page)) list = [o.page, ...list];
    return list.filter((p) => p && !isClosed(p));
  };

  const addEntry = (e) => {
    entries.push(e);
    if (entries.length > MAX_ENTRIES) entries.splice(0, entries.length - MAX_ENTRIES);
    return e;
  };

  const noteFailure = (key, why) => { state.failed = { at: Date.now(), key, why }; };

  function watch(page, pageIndex) {
    if (watched.has(page) || typeof page.on !== "function") return;
    const offs = [];
    const on = (ev, fn) => {
      page.on(ev, fn);
      offs.push(() => { try { if (typeof page.off === "function") page.off(ev, fn); else if (typeof page.removeListener === "function") page.removeListener(ev, fn); } catch (_) { /* gone */ } });
    };

    on("request", (req) => {
      try {
        if (state.closed) return;
        const rt = req.resourceType();
        const method = req.method();
        const nav = typeof req.isNavigationRequest === "function" && req.isNavigationRequest();
        if (rt !== "xhr" && rt !== "fetch" && !nav) return;
        const template = routeTemplate(req.url());
        const mutation = (rt === "xhr" || rt === "fetch") && MUTATING.has(method);
        const entry = addEntry({ m: method, t: template, rt: nav ? "document" : rt, pg: pageIndex, at: Date.now(), s: null, sz: null, open: true, mutation, shape: null });
        openReqs.set(req, entry);
        if (mutation) { state.started = true; state.mutationStartedAt = entry.at; }
      } catch (_) { /* not evidence */ }
    });

    on("response", async (resp) => {
      try {
        if (state.closed) return;
        const req = resp.request();
        const entry = openReqs.get(req);
        if (!entry) return; // started before the notebook opened: not this step's
        const status = resp.status();
        entry.s = status;
        try {
          const len = Number(resp.headers && resp.headers()["content-length"]);
          if (Number.isFinite(len)) entry.sz = len;
        } catch (_) { /* optional */ }
        if (!entry.mutation) return;
        const key = endpointKey(entry.m, entry.t);
        if (MUTATION_REFUSED.has(status) || status >= 500) { noteFailure(key, `status ${status}`); return; }
        // An OK answer whose reply says the action failed (storage full, quota, rejected). The body is read
        // once, reduced to its shape and dropped; only small JSON replies are looked at.
        const shape = await peekShape(resp, entry.sz);
        if (shape) entry.shape = shape;
        if (shape && shape.error_flag) noteFailure(key, `status ${status}, but the reply says the action failed`);
        else if (state.failed && state.failed.key === key) state.failed = null; // the app retried and got through
      } catch (_) { /* not evidence */ }
    });

    on("requestfinished", (req) => {
      try { const e = openReqs.get(req); if (e) { e.open = false; openReqs.delete(req); } } catch (_) { /* not evidence */ }
    });
    on("requestfailed", (req) => {
      try {
        const e = openReqs.get(req);
        if (!e) return;
        e.open = false;
        openReqs.delete(req);
        const why = (req.failure() && req.failure().errorText) || "request failed";
        if (e.mutation && !/ERR_ABORTED/i.test(why)) noteFailure(endpointKey(e.m, e.t), why);
      } catch (_) { /* not evidence */ }
    });
    on("download", () => { if (!state.closed) { state.downloads++; state.started = true; } });

    watched.set(page, { detach: () => offs.forEach((f) => f()) });
  }

  const nb = {
    sinceMs,
    baseline: null,

    /** Pick up tabs opened since the last call. Cheap; called on every sample. */
    refresh() {
      let i = watched.size;
      for (const p of livePages()) {
        if (watched.has(p)) continue;
        if (!initial.has(p)) { state.newPages++; state.started = true; }
        watch(p, i++);
        if (!urlAtOpen.has(p)) urlAtOpen.set(p, safeUrl(p));
      }
    },

    /**
     * What the screen shows right now: busy indicators and status text from every visible frame of every
     * page this run owns. Null when the step's own page cannot answer (closed, hung): that is not evidence
     * of anything. A frame or tab that cannot answer is simply left out.
     */
    async sample() {
      nb.refresh();
      let busyCount = 0;
      const regions = [];
      let stepPageAnswered = false;
      for (const p of livePages()) {
        const frames = (typeof p.frames === "function" && p.frames()) || [p];
        const main = typeof p.mainFrame === "function" ? p.mainFrame() : null;
        for (const f of frames.slice(0, MAX_FRAMES_PER_PAGE)) {
          const isMain = f === p || f === main;
          if (!isMain && typeof f.frameElement === "function") {
            // A frame that is not on screen (an ad, a hidden helper) says nothing about what the person sees.
            try { const el = await f.frameElement(); if (el && typeof el.isVisible === "function" && !(await el.isVisible())) continue; } catch (_) { /* include it */ }
          }
          let s = null;
          try { s = await evalOn(isMain ? p : f, pageScripts.completionProbe, undefined, 2000); } catch (_) { s = null; }
          if (!s || s === EVAL_TIMED_OUT) continue;
          if (isMain && p === o.page) stepPageAnswered = true;
          busyCount += s.busyCount || 0;
          for (const t of s.regions || []) if (regions.length < MAX_REGIONS && !regions.includes(t)) regions.push(t);
        }
      }
      if (!stepPageAnswered) return null;
      const before = new Set((nb.baseline && nb.baseline.regions) || []);
      for (const t of regions) if (!before.has(t) && seenNew.size < MAX_REGIONS * 4) seenNew.add(t);
      return { busyCount, regions };
    },

    /** Every text that appeared since the notebook opened, even one that has gone again (a "complete" toast). */
    seenNew() { return [...seenNew]; },

    /** Any sign the app took the work: a data-changing request, a download, a new tab, a new address. */
    sawStart() {
      if (state.started) return true;
      for (const [p, url0] of urlAtOpen) { const u = safeUrl(p); if (url0 && u && u !== url0) return true; }
      return false;
    },
    mutationStarted() { return state.mutationStartedAt > 0; },
    pendingMutations() { let n = 0; for (const e of openReqs.values()) if (e.mutation && e.open) n++; return n; },
    /** The last data-changing request this run lost, had refused, or that answered OK saying it failed. */
    mutationFailure() { return state.failed; },
    downloads() { return state.downloads; },
    newPages() { return state.newPages; },
    /** Metadata only: counts, never contents. Safe to log. */
    summary() {
      return {
        requests: entries.length,
        mutations: entries.filter((e) => e.mutation).length,
        downloads: state.downloads,
        newPages: state.newPages,
        failed: !!state.failed,
      };
    },
    entries() { return entries.map((e) => ({ ...e })); },

    close() {
      if (state.closed) return;
      state.closed = true;
      for (const w of watched.values()) w.detach();
      watched.clear();
      openReqs.clear();
    },
  };

  for (const p of livePages()) initial.add(p);
  nb.refresh();
  // A re-check after a pause measures against what the page showed BEFORE the action, not against the tray
  // that is on screen now (that would read as "nothing new").
  nb.baseline = o.baseline || await nb.sample();
  return nb;
}

/** One small JSON reply, read once and reduced to its shape. Never throws; null when it was not looked at. */
async function peekShape(resp, size) {
  try {
    if (typeof resp.body !== "function") return null;
    if (size != null && size > BODY_PEEK_MAX_BYTES) return null;
    let type = "";
    try { type = String((resp.headers && resp.headers()["content-type"]) || ""); } catch (_) { /* optional */ }
    if (type && !/json/i.test(type)) return null;
    const buf = await Promise.race([resp.body(), new Promise((resolve) => setTimeout(() => resolve(null), BODY_PEEK_TIMEOUT_MS))]);
    if (!buf || buf.length > BODY_PEEK_MAX_BYTES) return null;
    return bodyShape(JSON.parse(buf.toString("utf8")));
  } catch (_) { return null; }
}

module.exports = { open, bodyShape, MAX_ENTRIES };
