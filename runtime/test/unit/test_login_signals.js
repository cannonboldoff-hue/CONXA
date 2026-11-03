"use strict";

// Pure decision logic for interactive sign-in completion — see login_signals.js's own header for
// the vocabulary (lookouts, pause sign, judge, the ladder). No Playwright here: this exercises the
// yes/no/can't-tell rule against synthetic snapshots, the same way test_target_hosts.js exercises
// resolveTargetHosts offline.

const test = require("node:test");
const assert = require("node:assert");

const {
  ticketSignature,
  sameTickets,
  ticketsChanged,
  looksPaused,
  looksLikeLoginAnswer,
  sameAddress,
  judgeFromSnapshots,
  isSignedInAgainstBaseline,
  shouldAskJudge,
  alreadySignedIn,
  backupAgreementHeldMs,
  ladderVerdict,
  templatePath,
  statusMatchesSpec,
  evaluateAuthDefinition,
  classifySession,
  looksLikeSessionLogin,
  matchesSuccessUrl,
} = require("../../app/login_signals");

test("ticketSignature sorts names/keys so signature comparison isn't order-sensitive", () => {
  const sig = ticketSignature({ cookieNames: ["b", "a"], storageKeyCounts: { z: 1, a: 2 } });
  assert.deepStrictEqual(sig.cookieNames, ["a", "b"]);
  assert.deepStrictEqual(Object.keys(sig.storageKeyCounts), ["a", "z"]);
});

test("sameTickets compares by content, not object identity", () => {
  const a = ticketSignature({ cookieNames: ["session"], storageKeyCounts: { app: 2 } });
  const b = ticketSignature({ cookieNames: ["session"], storageKeyCounts: { app: 2 } });
  const c = ticketSignature({ cookieNames: ["session"], storageKeyCounts: { app: 3 } });
  assert.ok(sameTickets(a, b));
  assert.ok(!sameTickets(a, c));
  assert.ok(!sameTickets(null, b));
});

test("ticketsChanged fires the first time something real is seen, and again on any later diff", () => {
  const nothing = ticketSignature({});
  const first = ticketSignature({ cookieNames: ["session"] });
  assert.ok(!ticketsChanged(null, nothing)); // nothing before, nothing after — not a signal
  assert.ok(ticketsChanged(null, first)); // nothing before, something now — "a new ticket appeared"
  assert.ok(!ticketsChanged(first, first)); // unchanged
  const second = ticketSignature({ cookieNames: ["session", "csrf"] });
  assert.ok(ticketsChanged(first, second));
});

test("looksPaused recognises an OTP-style input group or the standard autocomplete marker", () => {
  assert.ok(looksPaused({ otpLikeInputCount: 6, hasOneTimeCodeAutocomplete: false }));
  assert.ok(looksPaused({ otpLikeInputCount: 0, hasOneTimeCodeAutocomplete: true }));
  assert.ok(!looksPaused({ otpLikeInputCount: 1, hasOneTimeCodeAutocomplete: false }));
  assert.ok(!looksPaused(null));
});

test("backupAgreementHeldMs is 0 until a SECOND lookout has fired", () => {
  assert.strictEqual(backupAgreementHeldMs({ passwordGone: 1000 }, 5000), 0);
  assert.strictEqual(backupAgreementHeldMs({}, 5000), 0);
});

test("backupAgreementHeldMs counts from the second-earliest fire, not the first or the latest", () => {
  // passwordGone fired at 1000, newTickets at 3000 — the "two agree" moment is 3000.
  const firedAt = { passwordGone: 1000, newTickets: 3000 };
  assert.strictEqual(backupAgreementHeldMs(firedAt, 13000), 10000);
});

test("ladder: paused overrides everything else, even a judge yes", () => {
  const v = ladderVerdict({ paused: true, judge: "yes", firedAt: { passwordGone: 0, newTickets: 0 }, nowMs: 20000 });
  assert.deepStrictEqual(v, { action: "wait", reason: "paused" });
});

test("ladder: judge yes saves immediately regardless of lookout agreement", () => {
  const v = ladderVerdict({ paused: false, judge: "yes", firedAt: {}, nowMs: 0 });
  assert.deepStrictEqual(v, { action: "save", reason: "judge_yes" });
});

test("ladder: judge no is a false alarm — keep watching even with lookouts fired", () => {
  const v = ladderVerdict({ paused: false, judge: "no", firedAt: { passwordGone: 0, newTickets: 0 }, nowMs: 20000 });
  assert.deepStrictEqual(v, { action: "wait", reason: "judge_no" });
});

test("ladder: can't-tell (judge null) falls back to the backup rule once two lookouts agree for 10s", () => {
  const firedAt = { passwordGone: 1000, newTickets: 2000 };
  assert.deepStrictEqual(
    ladderVerdict({ paused: false, judge: null, firedAt, nowMs: 11999 }),
    { action: "wait", reason: "insufficient_signal" },
  );
  assert.deepStrictEqual(
    ladderVerdict({ paused: false, judge: null, firedAt, nowMs: 12000 }),
    { action: "save", reason: "lookouts_agreed" },
  );
});

// The phone-approval screen: cookies written, password box gone, still on the sign-in pages.
test("ladder: never saves while the person is still on a sign-in page, even with a judge yes or agreed lookouts", () => {
  const firedAt = { passwordGone: 1000, newTickets: 2000 };
  assert.deepStrictEqual(ladderVerdict({ paused: false, onSignIn: true, judge: null, firedAt, nowMs: 999999 }),
    { action: "wait", reason: "on_sign_in" });
  assert.deepStrictEqual(ladderVerdict({ paused: false, onSignIn: true, judge: "yes", firedAt, nowMs: 999999 }),
    { action: "wait", reason: "on_sign_in" });
});

// A second-factor page whose address says nothing about signing in (GitHub's
// /sessions/two-factor/app): the lookouts all fired, the judge is still probing — never save yet.
test("ladder: the backup rule waits for the judge to ANSWER for the current page, never races it", () => {
  const firedAt = { passwordGone: 1000, newTickets: 2000, landed: 3000 };
  assert.deepStrictEqual(ladderVerdict({ paused: false, judge: null, judgeSettled: false, firedAt, nowMs: 999999 }),
    { action: "wait", reason: "judging" });
  assert.deepStrictEqual(ladderVerdict({ paused: false, judge: null, judgeSettled: true, firedAt, nowMs: 999999 }),
    { action: "save", reason: "lookouts_agreed" });
});

test("ladder: can't-tell with only one lookout never saves on the backup rule alone", () => {
  const v = ladderVerdict({ paused: false, judge: null, firedAt: { passwordGone: 1000 }, nowMs: 999999 });
  assert.deepStrictEqual(v, { action: "wait", reason: "insufficient_signal" });
});

// ── looksLikeLoginAnswer / judgeFromSnapshots / alreadySignedIn ─────────────────────────────────
// docs/artifacts/login-desk.html "Ask for the login page again" — the judge's before/after compare.
// Deliberately no "known IdP host" concept any more — see login_signals.js's header comment on why
// a hardcoded provider list was both incomplete and wrong (GitHub is often the app itself).

test("looksLikeLoginAnswer: a password box always counts, regardless of URL", () => {
  assert.ok(looksLikeLoginAnswer({ url: "https://app.acme-cloud.io/dashboard", hasPasswordBox: true }));
});

test("looksLikeLoginAnswer: a login-shaped path counts even with no password box (e.g. email-first)", () => {
  assert.ok(looksLikeLoginAnswer({ url: "https://app.acme.com/login", hasPasswordBox: false }));
});

test("looksLikeLoginAnswer: an ordinary app page with neither is not a login answer", () => {
  assert.ok(!looksLikeLoginAnswer({ url: "https://app.acme-cloud.io/dashboard", hasPasswordBox: false }));
});

test("looksLikeLoginAnswer: an unparseable url is treated as a login answer, never a false yes", () => {
  assert.ok(looksLikeLoginAnswer({ url: "", hasPasswordBox: false }));
});

// A dead session with a remembered account lands on Google's account chooser, which has no
// password box and lives under /v3/signin/ — the segment is not the FIRST one.
test("looksLikeLoginAnswer: a login segment at any depth counts (Google's /v3/signin/accountchooser)", () => {
  assert.ok(looksLikeLoginAnswer({ url: "https://accounts.google.com/v3/signin/accountchooser?continue=x", hasPasswordBox: false }));
  assert.ok(looksLikeLoginAnswer({ url: "https://accounts.google.com/v3/signin/identifier", hasPasswordBox: false }));
});

test("looksLikeLoginAnswer: only a WHOLE segment counts, never a longer word containing it", () => {
  assert.ok(!looksLikeLoginAnswer({ url: "https://app.acme.com/account/signin-methods", hasPasswordBox: false }));
  assert.ok(!looksLikeLoginAnswer({ url: "https://app.acme.com/settings/login-history", hasPasswordBox: false }));
});

test("isSignedInAgainstBaseline: a dead session's account chooser is NOT signed in, even though it differs from the cookie-less page", () => {
  const baseline = { url: "https://accounts.google.com/v3/signin/identifier", hasPasswordBox: true };
  const chooser = { url: "https://accounts.google.com/v3/signin/accountchooser", hasPasswordBox: false };
  assert.strictEqual(isSignedInAgainstBaseline(baseline, chooser), false);
});

test("judgeFromSnapshots: after is null (probe failed) -> can't tell", () => {
  assert.strictEqual(judgeFromSnapshots({ url: "https://app.acme.com/login", hasPasswordBox: true }, null), null);
});

test("judgeFromSnapshots: before is null (pre-sign-in probe failed) -> can't tell", () => {
  assert.strictEqual(judgeFromSnapshots(null, { url: "https://app.acme.com/dashboard", hasPasswordBox: false }), null);
});

// Mid-second-factor, the login address still asks for sign-in exactly as before — that is a "no",
// not "can't tell" (which used to hand the half-done sign-in to the backup rule).
test("judgeFromSnapshots: same address, still a login answer -> no", () => {
  const before = { url: "https://app.acme.com/login", hasPasswordBox: true };
  const after = { url: "https://app.acme.com/login?x=1", hasPasswordBox: true }; // query ignored
  assert.strictEqual(judgeFromSnapshots(before, after), "no");
});

test("judgeFromSnapshots: same address, no longer login-shaped -> can't tell (e.g. a root URL serving a page either way)", () => {
  const before = { url: "https://app.acme.com/", hasPasswordBox: false };
  const after = { url: "https://app.acme.com/", hasPasswordBox: false };
  assert.strictEqual(judgeFromSnapshots(before, after), null);
});

test("judgeFromSnapshots: bounced off the login page to the app -> yes", () => {
  const before = { url: "https://app.acme.com/login", hasPasswordBox: true };
  const after = { url: "https://app.acme.com/dashboard", hasPasswordBox: false };
  assert.strictEqual(judgeFromSnapshots(before, after), "yes");
});

test("judgeFromSnapshots: still shows a login-shaped answer at a different address -> no (false alarm)", () => {
  const before = { url: "https://app.acme.com/login", hasPasswordBox: true };
  const after = { url: "https://accounts.google.com/signin", hasPasswordBox: false };
  assert.strictEqual(judgeFromSnapshots(before, after), "no");
});

test("alreadySignedIn: the before snapshot already doesn't look like a login answer -> old login still works", () => {
  assert.ok(alreadySignedIn({ url: "https://app.acme-cloud.io/dashboard", hasPasswordBox: false }));
});

test("alreadySignedIn: a real login page, or no snapshot at all, is not already signed in", () => {
  assert.ok(!alreadySignedIn({ url: "https://app.acme.com/login", hasPasswordBox: true }));
  assert.ok(!alreadySignedIn(null));
});

// ── sameAddress / isSignedInAgainstBaseline ─────────────────────────────────────────────────────
// The mechanism that replaced host/redirect/IdP-list matching for pre-flight validation and the
// prover (browser.js::_isAuthenticated, ::_signedOutBaseline). `baseline` here is a GENUINE
// signed-out snapshot (fresh, cookie-less context), unlike judgeFromSnapshots's "before" — so
// "same as baseline" is a definite "still signed out", not merely inconclusive.

test("sameAddress: same origin+path, query/hash ignored", () => {
  assert.ok(sameAddress({ url: "https://a.test/x" }, { url: "https://a.test/x?y=1#z" }));
  assert.ok(!sameAddress({ url: "https://a.test/x" }, { url: "https://a.test/y" }));
  assert.ok(!sameAddress({ url: "https://a.test/x" }, { url: "https://b.test/x" }));
});

test("isSignedInAgainstBaseline: GitHub-shaped case — www. redirect no longer matters", () => {
  // The exact bug this replaced: the old check compared exact hostnames, so
  // https://www.github.com/login redirecting to https://github.com/ always read as "not signed
  // in", session or not. The baseline compare doesn't care what host either snapshot landed on.
  const baseline = { url: "https://www.github.com/login", hasPasswordBox: true };
  const current  = { url: "https://github.com/", hasPasswordBox: false };
  assert.strictEqual(isSignedInAgainstBaseline(baseline, current), true);
});

test("isSignedInAgainstBaseline: current equals the genuine signed-out baseline -> not signed in", () => {
  const baseline = { url: "https://github.com/login", hasPasswordBox: true };
  const current  = { url: "https://github.com/login", hasPasswordBox: true };
  assert.strictEqual(isSignedInAgainstBaseline(baseline, current), false);
});

test("isSignedInAgainstBaseline: current still login-shaped despite differing from baseline (e.g. next credential step) -> false", () => {
  const baseline = { url: "https://github.com/login", hasPasswordBox: true };
  const current  = { url: "https://github.com/login/two-factor", hasPasswordBox: false };
  assert.strictEqual(isSignedInAgainstBaseline(baseline, current), false);
});

test("isSignedInAgainstBaseline: no baseline (host-owned gap) falls back to best-effort", () => {
  assert.strictEqual(isSignedInAgainstBaseline(null, { url: "https://github.com/", hasPasswordBox: false }), true);
  assert.strictEqual(isSignedInAgainstBaseline(null, { url: "https://github.com/login", hasPasswordBox: true }), false);
});

test("isSignedInAgainstBaseline: no current snapshot (probe failed) -> false", () => {
  assert.strictEqual(isSignedInAgainstBaseline({ url: "https://github.com/login", hasPasswordBox: true }, null), false);
});

// ── shouldAskJudge ───────────────────────────────────────────────────────────────────────────
// The regression this guards: the judge used to be re-asked on EVERY tick once any lookout had
// fired, hammering the site's login page over and over (docs/artifacts/login-desk.html "Gentle
// with the website"). It must fire once per NEW lookout, then go quiet until another one fires.

test("shouldAskJudge: fires the first time a lookout has fired", () => {
  assert.ok(shouldAskJudge({ firedCount: 1, judgedAtFiredCount: 0, judgeVerdict: null, judging: false, paused: false }));
});

test("shouldAskJudge: does NOT re-fire tick after tick with no NEW lookout, even many ticks later", () => {
  const opts = { firedCount: 1, judgedAtFiredCount: 1, judgeVerdict: "no", judging: false, paused: false };
  assert.ok(!shouldAskJudge(opts));
  assert.ok(!shouldAskJudge(opts), "still no on a later tick — nothing changed");
});

test("shouldAskJudge: never while the person's own tab is still on a sign-in page", () => {
  assert.ok(!shouldAskJudge({ firedCount: 1, judgedAtFiredCount: 0, judgeVerdict: null, judging: false, paused: false, onSignIn: true }));
  assert.ok(shouldAskJudge({ firedCount: 1, judgedAtFiredCount: 0, judgeVerdict: null, judging: false, paused: false, onSignIn: false }));
});

// The judge said "no" on the second-factor page; the person then lands in the app — no lookout is
// left to fire, so the page change itself must trigger the re-ask.
test("shouldAskJudge: re-asks when the person's own tab moves to another page, not before any lookout fired", () => {
  const base = { firedCount: 3, judgedAtFiredCount: 3, judgeVerdict: "no", judging: false, paused: false };
  assert.ok(!shouldAskJudge({ ...base, address: "https://github.com/sessions/two-factor/app", judgedAtAddress: "https://github.com/sessions/two-factor/app" }));
  assert.ok(shouldAskJudge({ ...base, address: "https://github.com/", judgedAtAddress: "https://github.com/sessions/two-factor/app" }));
  assert.ok(!shouldAskJudge({ firedCount: 0, judgedAtFiredCount: 0, judgeVerdict: null, judging: false, paused: false, address: "b", judgedAtAddress: "a" }));
});

test("shouldAskJudge: fires again once a SECOND, different lookout fires", () => {
  assert.ok(shouldAskJudge({ firedCount: 2, judgedAtFiredCount: 1, judgeVerdict: "no", judging: false, paused: false }));
});

test("shouldAskJudge: never while paused, never while a previous ask is still in flight, never once judge already said yes", () => {
  assert.ok(!shouldAskJudge({ firedCount: 1, judgedAtFiredCount: 0, judgeVerdict: null, judging: false, paused: true }));
  assert.ok(!shouldAskJudge({ firedCount: 1, judgedAtFiredCount: 0, judgeVerdict: null, judging: true, paused: false }));
  assert.ok(!shouldAskJudge({ firedCount: 2, judgedAtFiredCount: 1, judgeVerdict: "yes", judging: false, paused: false }));
});

// ── templatePath / statusMatchesSpec ────────────────────────────────────────────────────────────

test("templatePath replaces numeric and uuid-shaped segments with :id, leaves the rest alone", () => {
  assert.strictEqual(templatePath("/api/users/48213/me"), "/api/users/:id/me");
  assert.strictEqual(templatePath("/api/orders/9c1e2b3a-000a-4b2c-8b1e-1234567890ab"), "/api/orders/:id");
  assert.strictEqual(templatePath("/api/me"), "/api/me");
  assert.strictEqual(templatePath(""), "/");
});

test("statusMatchesSpec matches an Nxx class or an exact status", () => {
  assert.ok(statusMatchesSpec(200, "2xx"));
  assert.ok(statusMatchesSpec(204, "2xx"));
  assert.ok(!statusMatchesSpec(401, "2xx"));
  assert.ok(statusMatchesSpec(401, "401"));
  assert.ok(!statusMatchesSpec(403, "401"));
});

// ── evaluateAuthDefinition — shared fixture with auth_learning.py's Python twin ─────────────────
// Same cases run through evaluate() in conxa-cloud/tests/test_auth_learning.py. If the two
// languages ever disagree, a definition Build Studio's self-test approved could still not fire
// (or wrongly fire) at runtime — see this function's own header comment in login_signals.js.

const fs = require("node:fs");
const path = require("node:path");
const AUTH_CASES = JSON.parse(
  fs.readFileSync(path.join(__dirname, "..", "fixtures", "auth_definition_cases.json"), "utf-8"),
);

for (const c of AUTH_CASES) {
  test(`evaluateAuthDefinition fixture: ${c.name}`, () => {
    const result = evaluateAuthDefinition(c.definition, c.observation);
    assert.strictEqual(result.verdict, c.expect.verdict, JSON.stringify(result));
  });
}

// ── classifySession — shared fixture with auth_learning.py::classify_session ────────────────────
// Same cases run through the Python twin in conxa-cloud/tests/test_session_check.py. One rule for
// pre-flight and for Build Studio's Check now / Check all / recording gate.
const SESSION_CASES = JSON.parse(
  fs.readFileSync(path.join(__dirname, "..", "fixtures", "session_check_cases.json"), "utf-8"),
);

for (const c of SESSION_CASES) {
  test(`classifySession fixture: ${c.name}`, () => {
    const state = classifySession(c.observation, c.baseline, c.app, { noBaselineOk: Boolean(c.noBaselineOk) });
    assert.strictEqual(state, c.expect.state);
  });
}

// ── login path fixture — shared with the recorder's drop in recorder/frame_utils.py ─────────────
// conxa-cloud/tests/test_recorder_drops_login_events.py runs the same file through the Python
// twins (LoginFlowTracker for the recording drop, auth_learning.looks_like_session_login here).
const LOGIN_PATH_CASES = JSON.parse(
  fs.readFileSync(path.join(__dirname, "..", "fixtures", "login_path_cases.json"), "utf-8"),
);

for (const c of LOGIN_PATH_CASES) {
  test(`looksLikeSessionLogin fixture: ${c.name}`, () => {
    assert.strictEqual(looksLikeSessionLogin({ final_url: c.url }), c.session_login);
  });
}

test("matchesSuccessUrl: a literal {} is a wildcard, anything else is a prefix", () => {
  assert.ok(matchesSuccessUrl("https://drive.google.com/drive/my-drive", "https://drive.google.com/{}"));
  assert.ok(!matchesSuccessUrl("https://accounts.google.com/", "https://drive.google.com/{}"));
  assert.ok(!matchesSuccessUrl("https://a.test/", ""));
  assert.ok(!matchesSuccessUrl("https://a.test/", "{}"));
});

test("evaluateAuthDefinition: no definition or no observation -> unsure, never a false yes", () => {
  assert.strictEqual(evaluateAuthDefinition(null, { final_url: "https://a.test/" }).verdict, "unsure");
  assert.strictEqual(evaluateAuthDefinition({ signed_out: {}, signed_in: {} }, null).verdict, "unsure");
});
