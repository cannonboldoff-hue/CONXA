"use strict";
// login_signals.js — pure decision logic for interactive sign-in completion detection.
//
// Shared by both wait implementations in browser.js (_waitForSessionLogin, the normal
// one-Chromium-per-run case, and _waitForInteractiveAuth, the standalone-browser fallback) so
// the two never grow independently-drifting copies of the same rule — the same reason
// second_opinion.js::build_try_dismiss_from_hint is the one place a try_dismiss branch gets
// built. Nothing here touches Playwright: signal *gathering* (reading page.url(), querying the
// DOM, diffing cookies) stays in browser.js/page_scripts.js; this module only decides what a
// gathered snapshot means, so it is unit-testable offline like target_hosts.js/resolver.js.
//
// Vocabulary (matches the "Login Desk" design doc, docs/artifacts/login-desk.html):
//   lookouts   — passive signals that something happened: password-box-gone, new tickets.
//   pause sign — a mid-sign-in screen (OTP code, MFA) is up; nothing below counts while it is.
//   judge      — the active check: re-request the login URL and see if it still shows a login form.
//   baseline   — a GENUINE signed-out snapshot of the login URL (fresh, cookie-less context),
//                used by pre-flight validation and the prover instead of any host/redirect
//                matching (see browser.js::_signedOutBaseline, isSignedInAgainstBaseline below).
//   timekeeper — wait for the ticket signature to stop changing before saving (see browser.js).
//   prover     — verify the saved session actually works (see browser.js::_proveSession).

// ── Tickets (cookies + storage keys) ────────────────────────────────────────────────────────
// Names and counts only — never values. Keeps the existing "secrets stay on the machine" rule
// (see runtime/app/auth_manager.js's own comments) intact: this never has to be redacted from a
// log because it never carries anything worth redacting.
function ticketSignature({ cookieNames = [], storageKeyCounts = {} } = {}) {
  const sortedCounts = {};
  for (const k of Object.keys(storageKeyCounts).sort()) sortedCounts[k] = storageKeyCounts[k];
  return { cookieNames: [...cookieNames].sort(), storageKeyCounts: sortedCounts };
}

function sameTickets(a, b) {
  if (!a || !b) return false;
  if (a.cookieNames.length !== b.cookieNames.length) return false;
  for (let i = 0; i < a.cookieNames.length; i++) {
    if (a.cookieNames[i] !== b.cookieNames[i]) return false;
  }
  const ak = Object.keys(a.storageKeyCounts);
  const bk = Object.keys(b.storageKeyCounts);
  if (ak.length !== bk.length) return false;
  for (const k of ak) {
    if (a.storageKeyCounts[k] !== b.storageKeyCounts[k]) return false;
  }
  return true;
}

// "A new ticket appeared" — true the first time anything is seen (before === null) as long as
// there IS something, and true whenever the signature differs from the previous one.
function ticketsChanged(before, after) {
  if (!after) return false;
  if (!before) return after.cookieNames.length > 0 || Object.values(after.storageKeyCounts).some((n) => n > 0);
  return !sameTickets(before, after);
}

// ── Pause sign ───────────────────────────────────────────────────────────────────────────────
// Scoped deliberately narrow to the one mid-sign-in screen with an unambiguous DOM shape: an
// OTP/texted-code entry (a run of single-character inputs, or the standard
// autocomplete="one-time-code" marker). "Choose an account" / "approve on your phone" screens
// have no comparably reliable structural signature without reading text — see page_scripts.js's
// pauseSignProbe for the exact query — so they are left to the judge/backup-agreement ladder
// below instead of a dedicated pause. ponytail: OTP-only pause sign; broaden if real logins show
// the journey/password-box lookouts firing early on an account-chooser or approval screen too
// often for the backup rule to catch cleanly.
function looksPaused(domProbe) {
  if (!domProbe) return false;
  return domProbe.otpLikeInputCount >= 2 || Boolean(domProbe.hasOneTimeCodeAutocomplete);
}

// ── Judge (before/after snapshot compare) ───────────────────────────────────────────────────────
// A snapshot is `{ url, hasPasswordBox }` — what the login entry URL showed at one point in time —
// or null if the probe that would have gathered it failed. Gathering (goto + password-box probe)
// lives in browser.js::_snapshotLoginEntry; this is the pure "what does it mean" half, same split
// as the rest of this file.
//
// A snapshot "looks like a login answer" when it has a login-shaped path (LOGIN_PATH_RE — a
// generic pattern, not a per-company list) or still shows a password box. Either means "not
// signed in, as far as this probe can tell". Deliberately has no notion of "known identity
// provider hosts": that list was both incomplete (couldn't know every SSO/OAuth provider a
// customer's stack might use) and wrong when an app IS its own sign-in host under a name that
// happens to match a general-purpose provider's own domain (e.g. github.com — GitHub is often
// the app itself, not a third party fronting one). The signed-out baseline compare below
// (isSignedInAgainstBaseline) is what replaced host-list reasoning; this function only classifies
// ONE snapshot in isolation, generically.
// A whole path segment at ANY depth, not just the first: Google's signed-out pages live under
// /v3/signin/ (identifier, accountchooser), and a dead session with a remembered account lands on
// the chooser — no password box, an address different from the cookie-less page — which the
// first-segment-only form read as "signed in".
const LOGIN_PATH_RE = /\/(login|signin|sign-in|session-expired)(\/|$)/i;
function looksLikeLoginAnswer({ url, hasPasswordBox } = {}) {
  if (hasPasswordBox) return true;
  try {
    const u = new URL(url);
    return LOGIN_PATH_RE.test(u.pathname);
  } catch (_) {
    return true; // no url to read — treat as "can't confirm signed-in", never as a false yes.
  }
}

// origin+path, ignoring query/hash (Google's per-step TL= token would otherwise read as a new page).
function addressOf(url) {
  try { const u = new URL(url); return u.origin + u.pathname; } catch (_) { return String(url || ""); }
}

// Same origin+path (ignoring query/hash) — "the login address gave the same answer both times".
function sameAddress(a, b) {
  try {
    const ua = new URL(a.url);
    const ub = new URL(b.url);
    return ua.origin === ub.origin && ua.pathname === ub.pathname;
  } catch (_) {
    return a.url === b.url;
  }
}

// The artifact's "Ask for the login page again" trick: compare what the login entry URL showed
// before sign-in against what it shows now.
//   after is null                        -> null   (the probe itself failed — can't tell)
//   after still looks like login         -> "no"   (the site still asks for sign-in — at the same
//                                                    address or another; mid-flow on a second
//                                                    factor reads exactly like this)
//   same address, no longer login-shaped -> null   (can't tell — e.g. a root URL that serves a
//                                                    landing page either way; backup rule decides)
//   otherwise                            -> "yes"  (bounced off to the app — signed in)
// Same-address-still-login used to be "can't tell", which handed a half-finished second-factor
// sign-in to the backup rule; pre-flight (isSignedInAgainstBaseline) already reads a login answer
// as signed out, so a site whose login page never moves a signed-in visitor on was never
// supportable anyway.
// `before` may also be null (the pre-sign-in snapshot itself failed) — in that case there is
// nothing to compare against, so this always returns null and the caller falls back to the
// backup-agreement rule (ladderVerdict below).
function judgeFromSnapshots(before, after) {
  if (!after) return null;
  if (!before) return null;
  if (looksLikeLoginAnswer(after)) return "no";
  return sameAddress(before, after) ? null : "yes";
}

// Pre-flight validation / the prover (browser.js::_isAuthenticated, ::_proveSession): unlike
// judgeFromSnapshots above — where "before" is merely "whatever this session showed before THIS
// sign-in attempt," which may itself already be signed in — `baseline` here is a GENUINE
// signed-out snapshot: the same login URL fetched from a fresh, cookie-less browser context (see
// browser.js::_signedOutBaseline). So "same as baseline" is a definite "still signed out," not
// merely inconclusive — no host list, no success_url, no redirect-hostname matching involved.
function isSignedInAgainstBaseline(baseline, current) {
  if (!current || looksLikeLoginAnswer(current)) return false;
  if (!baseline) return true; // host-owned session, no isolated context available — see
                               // browser.js::_signedOutBaseline's accepted gap (mirrors
                               // _proveSession's existing host-owned limitation).
  return !sameAddress(baseline, current);
}

// Should the judge be re-asked THIS tick? Only once something changed since the last ask — a NEW
// lookout fired (firedCount > judgedAtFiredCount; lookouts are monotonic), or, once any lookout has
// fired, this login's own tabs moved to a different page (`address` !== `judgedAtAddress`) — never
// while a previous probe is still in flight, never while paused, and never once the judge has
// already said yes. Gentle with the website (per the artifact's own "Safety" section): one probe
// per change, never one per poll tick. The address trigger is what lets a "no" given on a
// second-factor screen be revisited when the person finally lands in the app — no lookout is left
// to fire by then.
// `onSignIn`: one of this login's own tabs is part-way through the sign-in (moved past its first
// screen, still on a sign-in page — see browser.js::_sampleLookouts). Never probe then — the probe
// loads the login entry URL in the SAME cookie jar, which can reset a multi-step flow (Google's
// identifier -> password) while the person is mid-typing. Ask only once they've left the flow.
function shouldAskJudge({
  firedCount, judgedAtFiredCount, judgeVerdict, judging, paused, onSignIn = false, address = "", judgedAtAddress = "",
}) {
  const changed = firedCount > judgedAtFiredCount || (firedCount > 0 && address !== judgedAtAddress);
  return changed && judgeVerdict !== "yes" && !judging && !paused && !onSignIn;
}

// "Already signed in" — the very first thing checked, before any window is even shown: if the
// "before" snapshot itself doesn't look like a login answer, an old saved login still works and
// there is nothing to wait for. Mirrors the artifact's "Old login still works" row.
function alreadySignedIn(before) {
  return Boolean(before) && !looksLikeLoginAnswer(before);
}

// ── The decision ladder ──────────────────────────────────────────────────────────────────────
// `firedAt`: { passwordGone?: ms, newTickets?: ms } — first-fire timestamp per lookout, or
// absent/undefined if it hasn't fired. Lookouts are monotonic (once true, always true for the
// rest of one sign-in wait), so "two or more have agreed for `agreeMs`" reduces to "the
// SECOND-earliest fire time is at least `agreeMs` in the past".
function backupAgreementHeldMs(firedAt, nowMs) {
  const times = Object.values(firedAt || {}).filter((t) => typeof t === "number").sort((a, b) => a - b);
  if (times.length < 2) return 0;
  return Math.max(0, nowMs - times[1]);
}

const DEFAULT_BACKUP_AGREE_MS = 10000;

// `judge` is one of "yes" | "no" | null (not asked yet, or the before/after snapshots read the
// same — see judgeFromSnapshots above). Mirrors the artifact's "Who do we believe?" ladder
// exactly:
//   paused                                  -> wait, nothing counts
//   judge yes                               -> save
//   judge no                                -> wait (false alarm — a lookout fired but the site
//                                               still shows its login form)
//   judge null + 2 lookouts agreed >= agreeMs -> save (backup rule, for sites the judge can't read)
//   otherwise                               -> wait
// `onSignIn` (one of this login's own tabs is part-way through the sign-in) beats every rung: a
// sign-in is never finished while the person is still on it. Without this, a phone-approval screen —
// cookies already written, password box already gone — read as "two lookouts agree", the half-done
// session was saved, the tab closed, and the run reopened sign-in from the first page.
// `judgeSettled`: the judge has actually ANSWERED for the page the person is on now. The backup
// rule is for a judge that answered "can't tell" — never for one not yet asked or still probing,
// which is where the lookouts (they fire on every intermediate sign-in step) would otherwise win
// the race on a second-factor page whose address doesn't look like a login.
function ladderVerdict({
  paused, onSignIn = false, judge, judgeSettled = true, firedAt, nowMs, agreeMs = DEFAULT_BACKUP_AGREE_MS,
}) {
  if (paused) return { action: "wait", reason: "paused" };
  if (onSignIn) return { action: "wait", reason: "on_sign_in" };
  if (judge === "yes") return { action: "save", reason: "judge_yes" };
  if (judge === "no") return { action: "wait", reason: "judge_no" };
  if (!judgeSettled) return { action: "wait", reason: "judging" };
  const held = backupAgreementHeldMs(firedAt, nowMs);
  if (held >= agreeMs) return { action: "save", reason: "lookouts_agreed" };
  return { action: "wait", reason: "insufficient_signal" };
}

// ── Learned auth definitions (P0: Application Authentication Recording) ────────────────────────
// evaluateAuthDefinition is the RUNTIME half of a definition Build Studio learns at Connect time
// (see conxa_compile/auth_learning.py::learn/self_test for the Studio half that builds and
// verifies one). Twinned deliberately: Studio verifies a definition with THIS exact rule before
// saving it, and the runtime later applies THIS exact rule to decide when a login is done — if
// the two ever disagreed, a definition that passed Studio's self-test could still not fire at
// runtime. Kept in sync via one shared fixture,
// runtime/test/fixtures/auth_definition_cases.json, run by both languages' unit tests.
//
// A definition is learned from a contrast (a signed-in observation vs. a genuine signed-out one),
// not from the sign-in journey — see auth_learning.py's header for why. This function only
// classifies ONE observation against an already-learned definition; it never inspects live pages
// itself (that's browser.js's job, same split as the rest of this file).

// "/api/users/:id/me" style templating so a learned endpoint still matches when the live id
// differs from the one seen while learning. Mirrors auth_learning.py::template_path exactly —
// covered by the same fixture so the two can't drift silently.
const _ID_SEGMENT_RE = /^[0-9]+$|^[0-9a-f-]{8,}$/i;
function templatePath(path) {
  if (!path) return "/";
  return path
    .split("/")
    .map((seg) => (seg && _ID_SEGMENT_RE.test(seg) ? ":id" : seg))
    .join("/");
}

// spec is "2xx".."5xx" or an exact status like "401".
function statusMatchesSpec(status, spec) {
  if (!spec) return false;
  const m = /^([1-5])xx$/.exec(spec);
  if (m) return Math.floor(Number(status) / 100) === Number(m[1]);
  return Number(spec) === Number(status);
}

function _pathOf(url) {
  try { return new URL(url).pathname || "/"; } catch (_) { return null; }
}

// Deliberately conservative: a positive only when the path matches the learned signed-in address
// exactly. "Merely isn't the login path" was tried and rejected — it's the same weak reasoning
// AUTH-10 flagged in the old generic heuristic (a public, non-login page reads as "yes"), and
// combined with the equally-weak session class it could out-vote a real signed-out observation.
// The veto direction has no such risk, so it stays generic: any exact match to the known
// signed-out address is "no", regardless of what signed_in.final_path says.
function _urlClass(def, finalPath) {
  if (finalPath === null) return null;
  const out = def.signed_out || {};
  const inn = def.signed_in || {};
  if (out.final_path && finalPath === out.final_path) return "no";
  if (inn.final_path && finalPath === inn.final_path) return "yes";
  return null;
}

function _networkClass(def, responses) {
  const endpoints = (def.signed_in && def.signed_in.endpoints) || [];
  if (!endpoints.length || !responses || !responses.length) return null;
  let sawOk = false;
  for (const r of responses) {
    for (const ep of endpoints) {
      if ((ep.method || "GET").toUpperCase() !== (r.method || "GET").toUpperCase()) continue;
      if (templatePath(ep.path) !== templatePath(r.path)) continue;
      if (ep.denied && statusMatchesSpec(r.status, ep.denied)) return "no";
      if (ep.ok && statusMatchesSpec(r.status, ep.ok)) sawOk = true;
    }
  }
  return sawOk ? "yes" : null;
}

function _markerPresent(markers, wanted) {
  return (markers || []).some((m) => m.role === wanted.role && m.name === wanted.name);
}

function _domClass(def, observation) {
  if (observation.password_box) return "no";
  const outMarkers = (def.signed_out && def.signed_out.markers) || [];
  if (!outMarkers.length) return null;
  const anyPresent = outMarkers.some((w) => _markerPresent(observation.markers, w));
  return anyPresent ? "no" : "yes";
}

function _sessionClass(def, cookieNames) {
  const keys = def.session_keys || [];
  if (!keys.length) return null;
  const present = (cookieNames || []).some((c) => keys.includes(c));
  return present ? "yes" : null; // weak signal — never a veto, never counts alone
}

// { verdict: "yes" | "no" | "unsure", classes: string[] } — classes lists whichever fired
// positive (diagnostics only; never trusted secrets — class names, not values).
function evaluateAuthDefinition(definition, observation) {
  if (!definition || !observation) return { verdict: "unsure", classes: [] };
  if (observation.otp_like) return { verdict: "unsure", classes: [] };

  const results = {
    url: _urlClass(definition, _pathOf(observation.final_url)),
    network: _networkClass(definition, observation.responses),
    dom: _domClass(definition, observation),
    session: _sessionClass(definition, observation.cookie_names),
  };

  if (Object.values(results).includes("no")) {
    return { verdict: "no", classes: Object.keys(results).filter((k) => results[k] === "no") };
  }
  const positives = Object.keys(results).filter((k) => results[k] === "yes");
  const nonSession = positives.filter((k) => k !== "session");
  if (positives.length >= 2 && nonSession.length >= 1) {
    return { verdict: "yes", classes: positives };
  }
  return { verdict: "unsure", classes: positives };
}

// ── Session check (pre-flight, Check now / Check all, the recording gate) ───────────────────────
// ONE rule for "is this saved session still signed in?", written twice: here and in
// conxa_compile/auth_learning.py::classify_session. Both run the shared fixture
// runtime/test/fixtures/session_check_cases.json, so Build Studio's badge and a run's pre-flight
// cannot disagree about the same page.
//
// Wider than LOGIN_PATH_RE above on purpose: that one also drives the live sign-in ladder, where a
// false "still on a login page" would hold a finished sign-in open. A saved-session check can lean
// the other way, since the cost of a wrong "expired" is one extra sign-in.
const SESSION_LOGIN_PATH_RE = /\/(login|log-in|signin|sign-in|logon|signon|sso|oauth2?|auth|authorize|authenticate|servicelogin|session-expired)(\/|$)/i;
// Visible control names that only appear when nobody is signed in.
const SIGNED_OUT_MARKER_RE = /^(sign[-_ ]?in|log[-_ ]?in|sign[-_ ]?up|get started|continue with .+|sign in with .+|log in with .+)$/i;

// success_url may carry a literal "{}" wildcard ("https://drive.google.com/{}"); only the part
// before it is a prefix to match. Python twin: recorder/session.py::url_matches_pattern.
function matchesSuccessUrl(url, pattern) {
  const prefix = String(pattern || "").split("{}", 1)[0];
  return Boolean(prefix) && String(url || "").startsWith(prefix);
}

// A password box, or an address with a sign-in word as a whole path segment at any depth. An
// unreadable address is no evidence either way. Also what the settle loop in
// browser.js::_isAuthenticated watches, so a client-side redirect off the login page can finish.
function looksLikeSessionLogin(observation) {
  if (observation.password_box) return true;
  try {
    return SESSION_LOGIN_PATH_RE.test(new URL(observation.final_url).pathname);
  } catch (_) {
    return false;
  }
}

function _looksSignedOutSession(observation) {
  return looksLikeSessionLogin(observation)
    || (observation.markers || []).some((m) => SIGNED_OUT_MARKER_RE.test(String(m.name || "").trim()));
}

// "ready" | "expired" | "unverified". Only a positive confirmation is "ready"; anything that could
// not be read is "unverified", never "ready".
//   observation  the auth_definition observation shape (final_url, password_box, otp_like, markers,
//                responses, cookie_names) gathered after navigating to the app's login_url
//   baseline     { url, hasPasswordBox } the same login_url shows a cookie-less browser, or null
//   app          { success_url, auth_definition }
//   noBaselineOk Conxa Execute cannot open a cookie-less context, so it accepts "not login-shaped"
function classifySession(observation, baseline, app = {}, { noBaselineOk = false } = {}) {
  const obs = observation || {};
  const finalUrl = String(obs.final_url || "");
  if (!/^https?:\/\//i.test(finalUrl)) return "unverified"; // navigation never completed

  if (app.auth_definition) {
    // A learned rule decides alone. When it cannot tell, only positive sign-out evidence moves the
    // answer off "unverified": success page and baseline are the generic path's proof, not this one's.
    const verdict = evaluateAuthDefinition(app.auth_definition, obs).verdict;
    if (verdict === "yes") return "ready";
    if (verdict === "no" || _looksSignedOutSession(obs)) return "expired";
    return "unverified";
  }

  if (looksLikeSessionLogin(obs)) return "expired";
  if (matchesSuccessUrl(finalUrl, app.success_url)) return "ready";
  if (_looksSignedOutSession(obs)) return "expired";
  if (baseline) return sameAddress(baseline, { url: finalUrl }) ? "unverified" : "ready";
  return noBaselineOk ? "ready" : "unverified";
}

module.exports = {
  classifySession,
  looksLikeSessionLogin,
  matchesSuccessUrl,
  ticketSignature,
  sameTickets,
  ticketsChanged,
  looksPaused,
  looksLikeLoginAnswer,
  addressOf,
  sameAddress,
  judgeFromSnapshots,
  isSignedInAgainstBaseline,
  shouldAskJudge,
  alreadySignedIn,
  backupAgreementHeldMs,
  ladderVerdict,
  DEFAULT_BACKUP_AGREE_MS,
  templatePath,
  statusMatchesSpec,
  evaluateAuthDefinition,
};
