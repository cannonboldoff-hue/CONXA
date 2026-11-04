"use strict";
// The words that make an address a sign-in page, in one place. Python twin:
// packages/conxa-core/conxa_core/login_paths.py (read its header for the two lists). Same words in both,
// checked by conxa-cloud/tests/test_login_paths_parity.py; every use is pinned by
// runtime/test/fixtures/login_path_cases.json.
const SIGN_IN_PAGE = [
  "login", "log-in", "signin", "sign-in", "sign_in", "servicelogin", "session-expired",
  "accountchooser", "account-chooser", "session/new",
];
const SSO_HOP = ["auth", "oauth", "oauth2", "sso", "logout", "logon", "signon", "authorize", "authenticate"];

// A whole path segment at any depth: /v3/signin/identifier matches signin, /authors matches nothing.
function segmentRe(words) {
  // The words are plain letters, "-", "_" and "/", none of which needs escaping in a pattern.
  return new RegExp("/(" + words.join("|") + ")(/|$)", "i");
}

const SIGN_IN_PAGE_RE = segmentRe(SIGN_IN_PAGE);
const SSO_HOP_RE = segmentRe(SSO_HOP);
const ANY_RE = segmentRe([...SIGN_IN_PAGE, ...SSO_HOP]);

function pathOf(url) {
  try { return new URL(url).pathname; } catch (_) { return ""; }
}

module.exports = { SIGN_IN_PAGE, SSO_HOP, SIGN_IN_PAGE_RE, SSO_HOP_RE, ANY_RE, pathOf };
