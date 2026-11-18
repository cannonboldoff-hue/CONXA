"""Keep secrets out of what Conxa writes down: web addresses and test inputs (WF-8 secrets check, 2026-11-18).

A form sent by GET puts its fields in the next page's address (``done.html?pw=…&tok=…``). That address was stored in
the recording, the compiled workflow and the logs. Every address Conxa persists goes through ``scrub_urls``: a query
or fragment value is replaced when its name looks secret, or when it equals (by hash) a value the person typed into a
password-like box during the recording. Mirrored in runtime/app/redact.js for the runtime's logs.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable
from urllib.parse import unquote_plus

REDACTED = "REDACTED"
# Shown in place of a secret test input on a card, in the thread and in the saved workflow.
MASK = "••••••"

# Name parts that mark a secret in a web address.
_SECRET_PARAM_PARTS = frozenset({
    "pw", "pwd", "pass", "passwd", "password", "passcode", "passphrase", "secret", "token", "tok", "key", "apikey",
    "auth", "authorization", "session", "sessionid", "sid", "code", "otp", "pin", "cvv", "cvc", "sig", "signature",
    "jwt", "bearer", "credential", "credentials", "accesstoken", "refreshtoken", "idtoken",
})
# Narrower for a workflow's own inputs: a "zip code" or "sort key" a person types in must stay visible.
_SECRET_INPUT_PARTS = frozenset({
    "pw", "pwd", "pass", "passwd", "password", "passcode", "passphrase", "secret", "token", "tok", "apikey", "otp",
    "cvv", "cvc", "credential", "credentials",
})
# Short, common words that mean a secret only as the whole name ("?code=…", "?key=…"), never as part of one: a
# "country_code" or "sort_key" in an address the workflow opens must stay as it is.
_WHOLE_NAME_ONLY = frozenset({"code", "key", "pin", "sid", "session", "auth", "sig"})
_WORD_HINTS = ("password", "passwd", "token", "secret", "apikey")
_URL = re.compile(r"https?://[^\s\"'<>\\]+", re.IGNORECASE)


def _parts(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name or ""))   # userPassword -> user Password
    return [p for p in re.split(r"[^a-z0-9]+", spaced.lower()) if p]


def _looks_secret(name: str, parts: frozenset[str]) -> bool:
    words = _parts(name)
    joined = "".join(words)
    return (joined in parts or any(w in parts and w not in _WHOLE_NAME_ONLY for w in words)
            or any(h in joined for h in _WORD_HINTS))


def is_secret_param(name: str) -> bool:
    return _looks_secret(name, _SECRET_PARAM_PARTS)


def is_secret_input(name: str) -> bool:
    return _looks_secret(name, _SECRET_INPUT_PARTS)


def secret_hash(value: str) -> str:
    """How a typed secret is remembered while recording: never the value itself."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _scrub_pairs(raw: str, hashes: frozenset[str]) -> str:
    out = []
    for seg in raw.split("&"):
        name, eq, value = seg.partition("=")
        if eq and value and (is_secret_param(unquote_plus(name)) or secret_hash(unquote_plus(value)) in hashes):
            seg = f"{name}={REDACTED}"
        out.append(seg)
    return "&".join(out)


def scrub_url(url: str, secret_hashes: Iterable[str] = ()) -> str:
    """The address with every secret-looking query or fragment value replaced; everything else byte for byte."""
    if "?" not in url and "#" not in url:
        return url
    hashes = frozenset(secret_hashes)
    head, hsep, frag = url.partition("#")
    base, qsep, query = head.partition("?")
    if qsep:
        head = f"{base}?{_scrub_pairs(query, hashes)}"
    if hsep and "=" in frag:
        frag = _scrub_pairs(frag, hashes)
    return f"{head}{hsep}{frag}"


def scrub_urls(obj: Any, secret_hashes: Iterable[str] = ()) -> Any:
    """``obj`` (JSON-shaped) with every web address in every string scrubbed. Returns a new object."""
    hashes = frozenset(secret_hashes)
    if isinstance(obj, str):
        return _URL.sub(lambda m: scrub_url(m.group(0), hashes), obj) if "://" in obj else obj
    if isinstance(obj, dict):
        return {k: scrub_urls(v, hashes) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_urls(v, hashes) for v in obj]
    return obj


def mask_secret_inputs(inputs: dict[str, Any] | None) -> dict[str, Any]:
    """Test inputs as they may be saved: a secret one keeps its name, never its value."""
    return {k: (MASK if is_secret_input(k) and v not in (None, "") else v) for k, v in (inputs or {}).items()}
