// The login tab always opens at login_url — AUTH-5 used to special-case an app whose sign-in
// lives on a different host than the app (Google Drive: accounts.google.com -> drive.google.com)
// by opening the app's success URL instead, so a host-based check would have something to see
// there. Detection no longer depends on where the user lands (see _signedOutBaseline /
// isSignedInAgainstBaseline in login_signals.js): the judge re-probes login_url itself, before vs.
// after, so opening login_url directly works uniformly — including for an app like GitHub, which
// IS its own sign-in host, not a third party fronting one.
const test   = require("node:test");
const assert = require("node:assert");

const { _loginEntryUrl, _successPrefix, _probeInSession } = require("../../app/browser");

const GITHUB = { login_url: "https://github.com/login", success_url: "https://github.com/{}" };
const DRIVE  = { login_url: "https://accounts.google.com", success_url: "https://drive.google.com/{}" };

test("_loginEntryUrl is always login_url, same-host or cross-host", () => {
  assert.strictEqual(_loginEntryUrl(GITHUB), "https://github.com/login");
  assert.strictEqual(_loginEntryUrl(DRIVE), "https://accounts.google.com");
  assert.strictEqual(_loginEntryUrl({ login_url: "https://a.test/login", success_url: "" }), "https://a.test/login");
});

test("_successPrefix strips the {} wildcard and tolerates a missing success_url", () => {
  assert.strictEqual(_successPrefix(DRIVE), "https://drive.google.com/");
  assert.strictEqual(_successPrefix({ success_url: "https://a.test/home" }), "https://a.test/home");
  assert.strictEqual(_successPrefix({}), "");
});

// A throwaway page with a fixed url/password-box answer, plus a throwaway incognito context that
// hands out pages of its own — enough to drive both _probeInSession's own probe tab (via
// session.context.newPage) and _signedOutBaseline's separate cookie-less fetch (via
// session.browser.newContext), the two contexts the new baseline compare needs.
function fakePage(url, hasPasswordBox) {
  return {
    goto: async () => {}, url: () => url, isClosed: () => false, close: async () => {},
    evaluate: async () => hasPasswordBox,
    // _isAuthenticated attaches a response listener before it navigates, then lets the page settle.
    on: () => {}, off: () => {}, waitForTimeout: async () => {},
  };
}
function fakeSession({ probeUrl, probeHasPasswordBox, baselineUrl, baselineHasPasswordBox }) {
  return {
    hostOwned: false,
    context: { newPage: async () => fakePage(probeUrl, probeHasPasswordBox) },
    browser: {
      newContext: async () => ({
        newPage: async () => fakePage(baselineUrl, baselineHasPasswordBox),
        close: async () => {},
      }),
    },
  };
}

test("_probeInSession navigates to login_url and reports signed-in via the baseline compare", async () => {
  // A genuinely signed-out baseline still shows the login form; the session's own probe has
  // bounced off it entirely — that's what "signed in" means now, no host/redirect matching.
  // A distinct login_url from the next test — _signedOutBaseline caches per entryUrl.
  const loginUrl = "https://github-signed-in.test/login";
  const session = fakeSession({
    probeUrl: "https://github-signed-in.test/", probeHasPasswordBox: false,
    baselineUrl: loginUrl, baselineHasPasswordBox: true,
  });
  const outcomes = await _probeInSession(session, [{ key: "ws__github", navUrl: loginUrl, label: "GitHub" }]);
  assert.strictEqual(outcomes.get("ws__github"), true);
});

test("_probeInSession reports NOT signed-in when the probe still shows a password box", async () => {
  const loginUrl = "https://github-signed-out.test/login";
  const session = fakeSession({
    probeUrl: loginUrl, probeHasPasswordBox: true,
    baselineUrl: loginUrl, baselineHasPasswordBox: true,
  });
  const outcomes = await _probeInSession(session, [{ key: "ws__github", navUrl: loginUrl, label: "GitHub" }]);
  assert.strictEqual(outcomes.get("ws__github"), false);
});
