"""The words that make an address a sign-in page, in one place.

Two lists, because they answer two different questions:

* SIGN_IN_PAGE - a page that is a sign-in page by name. Safe to act on by itself.
* SSO_HOP - the stops a sign-in passes through (/auth, /oauth2, /sso, /logout). Inside an app these are
  also ordinary pages (/settings/auth), so each caller counts them only with a second piece of evidence
  (a sign-in already under way, a password box, a sign-in title) or where a wrong "yes" is cheap.

Each caller names which question it is asking by the regex it picks. JavaScript twin:
runtime/app/login_paths.js. They must hold the same words; conxa-cloud/tests/test_login_paths_parity.py
checks that, and runtime/test/fixtures/login_path_cases.json pins every use in both languages.
"""

from __future__ import annotations

import re

SIGN_IN_PAGE = (
    "login", "log-in", "signin", "sign-in", "sign_in", "servicelogin", "session-expired",
    "accountchooser", "account-chooser", "session/new",
)
SSO_HOP = ("auth", "oauth", "oauth2", "sso", "logout", "logon", "signon", "authorize", "authenticate")


def _segment_re(words: tuple[str, ...]) -> re.Pattern[str]:
    """A whole path segment at any depth: /v3/signin/identifier matches signin, /authors matches nothing."""
    return re.compile(r"/(" + "|".join(re.escape(w) for w in words) + r")(/|$)", re.IGNORECASE)


SIGN_IN_PAGE_RE = _segment_re(SIGN_IN_PAGE)
SSO_HOP_RE = _segment_re(SSO_HOP)
ANY_RE = _segment_re(SIGN_IN_PAGE + SSO_HOP)
