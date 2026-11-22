"""The user name a person types in a sign-in window, kept only as salted hashes (AUTH-24 (4)).

A signed-in page shows its account ("Logged in as tomsmith"), and that text was kept as step context in packages
other people run. The sign-in window reads what is typed into the user-name box (never a password or other secret
box: SCRIPT decides that before it reads a value) and keeps only hashes of the lowercased value and of its word
pieces, in the group's private sign-in folder (locked like a saved sign-in, never part of a build). The compiler
drops context text that has one of those words in it, by comparing hashes: no list of names.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import string
from pathlib import Path
from typing import Any

from conxa_compile.saved_session import read_state, write_state

FILE = "signin-names.sealed"
BINDING = "__conxaSignInName"
_WORD = re.compile(r"\w+")
# shortcut: pieces under 3 characters are not kept, so "jo" alone is never scrubbed; lower it if a real 2-letter
# account name is seen on a signed-in page.
_MIN = 3

# Installed only in the sign-in window. The box must be a plain text or email box, must not look like a secret by
# the recorder's own rule (bridge.js isSensitiveEditable) or conxa_core.redact's input words, and must be a user-name
# box by the page's own markup: an email box, autocomplete "username"/"email", or the last text box before the first
# password box. The value is read only after all of that passed; a password box's value is never read.
# shortcut: a sign-in that asks for the name on one page and shows the password box only later is caught only when
# the name box is an email box or says autocomplete="username"; widen if a real site's name slips through.
SCRIPT = r"""
(() => {
  if (window.__conxaSignInNameV1) return;
  window.__conxaSignInNameV1 = true;
  const SECRET = /(^|[^a-z0-9])(pass|passwd|password|passcode|passphrase|pw|pwd|otp|secret|token|tok|apikey|cvv|cvc|credentials?|one-time-code)([^a-z0-9]|$)/;
  const HINT = /password|passwd|token|secret|apikey/;
  const looksSecret = (el) => {
    const hay = ["autocomplete", "name", "id", "aria-label", "placeholder"]
      .map((a) => String(el.getAttribute(a) || "").replace(/([a-z0-9])([A-Z])/g, "$1 $2").toLowerCase())
      .join(" ");
    return SECRET.test(hay) || HINT.test(hay.replace(/[^a-z0-9]+/g, ""));
  };
  const nameBox = (el) => {
    if (!el || el.tagName !== "INPUT") return false;
    const type = String(el.getAttribute("type") || "text").toLowerCase();
    if (type !== "text" && type !== "email") return false;
    if (looksSecret(el)) return false;
    const auto = String(el.getAttribute("autocomplete") || "").toLowerCase().split(/\s+/);
    if (type === "email" || auto.includes("username") || auto.includes("email")) return true;
    // Otherwise only the last text box before the first password box (in its form, else its page).
    const scope = el.form || el.getRootNode();
    const boxes = Array.from((scope && scope.querySelectorAll) ? scope.querySelectorAll("input") : []);
    const kind = (b) => String(b.getAttribute("type") || "text").toLowerCase();
    const pw = boxes.findIndex((b) => kind(b) === "password");
    const before = boxes.slice(0, Math.max(pw, 0)).filter((b) => kind(b) === "text" || kind(b) === "email");
    return pw > 0 && before[before.length - 1] === el;
  };
  const report = (el) => {
    try {
      if (!nameBox(el)) return;
      const v = String(el.value || "").trim();
      const fn = window["__conxaSignInName"];
      if (v && typeof fn === "function") fn(v);
    } catch (_e) {}
  };
  document.addEventListener("change", (e) => report(e.composedPath()[0]), true);
  document.addEventListener("submit", (e) => {
    try { for (const el of e.target.querySelectorAll("input")) report(el); } catch (_e) {}
  }, true);
})();
"""


def _pieces(value: str) -> set[str]:
    v = " ".join(str(value or "").split()).casefold()
    local = v.rsplit("@", 1)[0]  # an address's mail host is not the person; the address itself is scrubbed already
    pieces = {p for p in _WORD.findall(local) if len(p) >= _MIN}
    if len(v) >= _MIN:
        pieces.add(v)
    return pieces


def _hash(salt: str, word: str) -> str:
    return hashlib.sha256(f"{salt}\0{word}".encode("utf-8")).hexdigest()


def remember(folder: str | Path, value: str) -> None:
    """Add the hashes of `value` and its word pieces to `folder`'s locked names file. The value is not kept."""
    path = Path(folder) / FILE
    try:
        rec = read_state(path)
    except (OSError, ValueError):
        rec = {}
    salt = str(rec.get("salt") or "") or secrets.token_hex(16)
    old = set(rec.get("hashes") or [])
    new = old | {_hash(salt, p) for p in _pieces(value)}
    if new != old:
        write_state(path, {"salt": salt, "hashes": sorted(new)})


def load(data_dir: str | Path) -> list[tuple[str, frozenset[str]]]:
    """Every group's (salt, hashes). A file that cannot be opened here counts as none."""
    out: list[tuple[str, frozenset[str]]] = []
    for path in Path(data_dir).glob(f"groups/*/auth/{FILE}"):
        try:
            rec = read_state(path)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("salt") and rec.get("hashes"):
            out.append((str(rec["salt"]), frozenset(rec["hashes"])))
    return out


def mentions(text: Any, names: list[tuple[str, frozenset[str]]]) -> bool:
    """True when `text` has a remembered user name, or one of its word pieces, as a whole word (any case)."""
    if not names or not isinstance(text, str) or not text.strip():
        return False
    t = text.casefold()
    words = set(_WORD.findall(t)) | {w.strip(string.punctuation) for w in t.split()}
    return any(_hash(salt, w) in hashes for salt, hashes in names for w in words if w)
