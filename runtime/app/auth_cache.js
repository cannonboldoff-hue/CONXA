"use strict";
const fs = require("fs");
const path = require("path");

// On-disk hints kept beside the saved sessions. browser.js passes its own sessions dir so there is
// one source of truth for where that is.
module.exports = function authCache(sessionsDir) {
  const read = (file) => { try { return JSON.parse(fs.readFileSync(path.join(sessionsDir, file), "utf8")); } catch (_) { return {}; } };
  const update = (file, key, value) => {
    const cache = read(file);
    cache[key] = value;
    fs.mkdirSync(sessionsDir, { recursive: true });
    fs.writeFileSync(path.join(sessionsDir, file), JSON.stringify(cache));
  };

  // ─── Auth-validation TTL cache ──────────────────────────────────────────────
  // A successful network validation is stamped per app key; repeat runs within
  // CONXA_AUTH_VALIDATION_TTL_MS (default 10 min) skip the live check entirely. 10 min matches
  // Build Studio's recording gate (handlers/session.py::_PROBE_TTL_S), so both sides trust a
  // "signed in" answer for the same time. The stamp records the session file's mtime — a fresh
  // interactive login rewrites that file, invalidating the cached verdict (which described the OLD state).
  const AUTH_VALIDATION_TTL_MS = Number(process.env.CONXA_AUTH_VALIDATION_TTL_MS) || 10 * 60 * 1000;
  const VALIDATION_FILE = "_auth_validation_cache.json";

  function readValidationCache(key, sessionPath) {
    try {
      const entry = read(VALIDATION_FILE)[key];
      if (!entry || typeof entry.validatedAt !== "number") return 0;
      if (Date.now() - entry.validatedAt > AUTH_VALIDATION_TTL_MS) return 0;
      if (Math.abs(fs.statSync(sessionPath).mtimeMs - entry.sessionMtimeMs) > 1) return 0;
      return entry.validatedAt;
    } catch (_) {
      return 0;
    }
  }
  function writeValidationCache(key, sessionPath) {
    try { update(VALIDATION_FILE, key, { validatedAt: Date.now(), sessionMtimeMs: fs.statSync(sessionPath).mtimeMs }); } catch (_) {}
  }
  // A run that died on this app's login wall proved the stamp wrong: drop it, or the next call skips
  // the live check for up to the TTL and fails identically.
  function clearValidation(key) {
    try {
      const cache = read(VALIDATION_FILE);
      if (!(key in cache)) return;
      delete cache[key];
      fs.writeFileSync(path.join(sessionsDir, VALIDATION_FILE), JSON.stringify(cache));
    } catch (_) {}
  }

  // ─── Remembered landing address ─────────────────────────────────────────────
  // The first real stop after a sign-in, kept only as a NAVIGATION HINT for server.js's post-login
  // pre-navigate — it plays no part in sign-in detection. Same shape as the cache above.
  const LANDING_FILE = "_landing_urls.json";
  const readLanding = (key) => read(LANDING_FILE)[key] || "";
  const writeLanding = (key, origin) => { try { update(LANDING_FILE, key, origin); } catch (_) {} };

  return { AUTH_VALIDATION_TTL_MS, readValidationCache, writeValidationCache, clearValidation, readLanding, writeLanding };
};
