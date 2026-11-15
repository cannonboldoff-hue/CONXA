"use strict";
/**
 * policy_gate.js — PROD-18 pre-action policy gate: execution-time windows and platform
 * deny-lists, checked before a run is admitted. `evaluate()` is pure decision logic with
 * no I/O — the same split resolver.js/resolve_adapter.js use — so window/deny-list rules
 * are unit-testable without a browser or network. `loadPolicy()` does the I/O: fetch,
 * verify, cache.
 *
 * Trust model: the policy document is Ed25519-signed by the cloud and verified with the
 * SAME public key baked into the host exe that already verifies the update manifest
 * (manifest_manager.js::verifyManifestSignature) — no new crypto, no new trust anchor. A
 * document that fails verification is discarded exactly like a bad manifest.
 *
 * "Enforce locally, prove centrally" (see docs/Audit-and-Control.md): the on-disk cache
 * lives on a machine the customer controls, and nothing here can detect it being deleted.
 * What IS locally enforceable is a policy's own `expires_at` — a customer cannot forge a
 * longer-lived policy without the private key, so "delete the cache and hope" does not
 * silently keep a governed workspace running forever. The bypass that genuinely can't be
 * caught locally (block the network before the policy ever expires) is what the `pol`
 * field carried on every evidence batch (tracker.js) and server-side reconciliation are
 * for — a workspace the server issued policy to, reporting no policy, is visible there.
 */
const fs = require("fs");
const path = require("path");
const manifestManager = require("./manifest_manager");
const httpClient = require("./http_client");

const CACHE_MAX_AGE_MS = 60 * 60 * 1000; // re-fetch in the background once cache is >1h old

// When a policy fetch last failed, per cloud address + workspace; see loadPolicy.
const _fetchFailedAt = new Map();
const FETCH_FAILURE_COOLDOWN_MS = Number(process.env.CONXA_POLICY_FAIL_COOLDOWN_MS) || 60 * 1000;

function _cachePath(dataDir, workspaceId) {
  return path.join(dataDir, "policy", `${workspaceId}.json`);
}

function _readCache(dataDir, workspaceId) {
  try {
    return JSON.parse(fs.readFileSync(_cachePath(dataDir, workspaceId), "utf8"));
  } catch (_) {
    return null;
  }
}

// doc === null clears the cache (used when the server confirms "no policy for this
// workspace", so a since-revoked policy doesn't keep enforcing from a stale local file).
function _writeCache(dataDir, workspaceId, doc) {
  try {
    const p = _cachePath(dataDir, workspaceId);
    if (doc === null) { try { fs.unlinkSync(p); } catch (_) {} return; }
    fs.mkdirSync(path.dirname(p), { recursive: true });
    const tmp = `${p}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(doc));
    fs.renameSync(tmp, p);
  } catch (_) {}
}

async function _fetchAndVerify({ apiUrl, workspaceId, trackingToken, publicKeyB64, timeoutMs }) {
  let doc;
  try {
    doc = await httpClient.fetchJSON(
      `${apiUrl}/api/v1/tracking/${encodeURIComponent(workspaceId)}/policy`,
      { headers: { "X-Tracking-Token": trackingToken || "" }, timeoutMs: timeoutMs || 5000 }
    );
  } catch (e) {
    if (/HTTP 404/.test((e && e.message) || "")) return { status: "none" }; // genuinely ungoverned
    return { status: "fetch_failed" };
  }
  if (!manifestManager.verifyManifestSignature(doc, publicKeyB64)) return { status: "invalid_signature" };
  return { status: "ok", doc };
}

/**
 * loadPolicy({ dataDir, apiUrl, workspaceId, trackingToken, publicKeyB64, nowMs, forceFetch })
 *   → { status: "fresh"|"cache"|"expired"|"absent", doc: object|null }
 *
 * "fresh"   fetched and verified just now; cache updated.
 * "cache"   using a previously verified, still-unexpired cache (fetch skipped or failed).
 * "expired" a verified cache exists but its own `expires_at` has passed, and a fresh
 *           fetch either failed or is still expired. The one state a policy-gated skill
 *           must refuse in — see the module doc above.
 * "absent"  no verified policy locally or on the server. Ordinary workspaces live here
 *           permanently and are unaffected by any of this.
 */
async function loadPolicy(opts = {}) {
  const { dataDir, apiUrl, workspaceId, trackingToken, publicKeyB64, forceFetch = false } = opts;
  const now = opts.nowMs != null ? opts.nowMs : Date.now();
  const cached = _readCache(dataDir, workspaceId);
  const cacheFresh = cached && (now - (cached._cached_at || 0)) < CACHE_MAX_AGE_MS;
  const cacheExpired = cached && (cached.expires_at || 0) * 1000 < now;

  if (cached && cacheFresh && !cacheExpired && !forceFetch) {
    return { status: "cache", doc: cached };
  }

  // An unreachable cloud used to cost every run the full fetch timeout (5 s) before it started. After a failed
  // fetch the next ones are skipped for a short while. A governed workspace is unaffected in what it enforces: it
  // already falls back to its verified cache (and to "expired" when that runs out) whenever a fetch fails.
  const failKey = `${apiUrl}|${workspaceId}`;
  const failedAt = _fetchFailedAt.get(failKey);
  if (!forceFetch && failedAt != null && now - failedAt < FETCH_FAILURE_COOLDOWN_MS) {
    if (cached) return { status: (cached.expires_at || 0) * 1000 < now ? "expired" : "cache", doc: cached };
    return { status: "absent", doc: null };
  }

  const result = await _fetchAndVerify({ apiUrl, workspaceId, trackingToken, publicKeyB64 });
  if (result.status === "fetch_failed") _fetchFailedAt.set(failKey, now);
  else _fetchFailedAt.delete(failKey);
  if (result.status === "ok") {
    const doc = { ...result.doc, _cached_at: now };
    _writeCache(dataDir, workspaceId, doc);
    return { status: "fresh", doc };
  }
  if (result.status === "none") {
    _writeCache(dataDir, workspaceId, null);
    return { status: "absent", doc: null };
  }
  // Fetch failed or a bad signature — fall back to whatever verified cache exists.
  if (cached) {
    return { status: (cached.expires_at || 0) * 1000 < now ? "expired" : "cache", doc: cached };
  }
  return { status: "absent", doc: null };
}

// ── Pure evaluation ──────────────────────────────────────────────────────────

const _DOW = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 7 };

function _localTimeInZone(nowMs, tz) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz, weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date(nowMs));
  const map = {};
  for (const p of parts) map[p.type] = p.value;
  const hour = map.hour === "24" ? "00" : map.hour; // some ICU builds format midnight as "24"
  return { dow: _DOW[map.weekday], hhmm: `${hour.padStart(2, "0")}:${map.minute.padStart(2, "0")}` };
}

// Self-check that Intl actually has real timezone data (a pkg-bundled host exe built
// without full-icu would silently evaluate every zone as UTC otherwise — see the
// PROD-18 plan's ICU risk note). Checked against a fixed instant and two zones whose
// correct local times are known, not against "does the constructor throw."
function probeTimezoneSupport() {
  try {
    const instant = Date.UTC(2024, 0, 1, 12, 0, 0); // 2024-01-01T12:00:00Z
    const ist = _localTimeInZone(instant, "Asia/Kolkata").hhmm;   // UTC+5:30 → 17:30
    const pst = _localTimeInZone(instant, "America/Los_Angeles").hhmm; // UTC-8 → 04:00
    return ist === "17:30" && pst === "04:00";
  } catch (_) {
    return false;
  }
}

function _skillMatches(windowSkills, skill) {
  if (!windowSkills || windowSkills.length === 0) return true;
  return windowSkills.some((pat) => {
    if (pat === "*") return true;
    if (pat.endsWith("*")) return skill.startsWith(pat.slice(0, -1));
    return pat === skill;
  });
}

function _withinWindow(win, nowMs) {
  const { dow, hhmm } = _localTimeInZone(nowMs, win.tz);
  if (!(win.days || []).includes(dow)) return false;
  return hhmm >= win.start && hhmm <= win.end;
}

/**
 * evaluate(doc, { skills, hosts, nowMs }) → { allow: true } | { allow: false, code, message }
 *
 * `doc` must already be a verified, non-expired policy (callers route "absent"/"expired"
 * from loadPolicy() before ever calling this). Windows are RESTRICTIONS, not an
 * allow-list: a skill matched by no window is unrestricted by this policy's window rules.
 */
function evaluate(doc, { skills = [], hosts = [], nowMs } = {}) {
  const now = nowMs != null ? nowMs : Date.now();
  const windows = doc.windows || [];
  const deniedHosts = new Set((doc.denied_hosts || []).map((h) => String(h).toLowerCase()));

  if (deniedHosts.size > 0) {
    if (hosts.length === 0) {
      return {
        allow: false, code: "host_unresolved",
        message: `Refused by workspace policy: this skill's target host(s) could not be determined, and this workspace has a platform deny-list configured (policy version ${doc.policy_version}). Refusing rather than risk running against a denied host.`,
      };
    }
    const hit = hosts.find((h) => deniedHosts.has(String(h).toLowerCase()));
    if (hit) {
      return {
        allow: false, code: "denied_host",
        message: `Refused by workspace policy: this skill interacts with ${hit}, which is on your organization's platform deny-list (policy version ${doc.policy_version}).`,
      };
    }
  }

  if (windows.length > 0 && !probeTimezoneSupport()) {
    return {
      allow: false, code: "policy_tz_unsupported",
      message: `Refused by workspace policy: this workspace has an execution-window policy, but this runtime cannot reliably evaluate timezones (policy version ${doc.policy_version}). Contact support.`,
    };
  }

  for (const skill of skills) {
    const applicable = windows.filter((w) => _skillMatches(w.skills, skill));
    if (applicable.length === 0) continue;
    if (!applicable.some((w) => _withinWindow(w, now))) {
      const w = applicable[0];
      return {
        allow: false, code: "outside_window",
        message: `Refused by workspace policy: "${skill}" may only run on day(s) ${w.days.join(",")} (1=Mon..7=Sun) between ${w.start}-${w.end} ${w.tz}. It is currently outside every allowed window for this skill (policy version ${doc.policy_version}).`,
      };
    }
  }

  return { allow: true };
}

module.exports = {
  _resetFetchFailures: () => _fetchFailedAt.clear(), loadPolicy, evaluate, probeTimezoneSupport, _localTimeInZone };
