"""One plain sentence for a failed test, for the conversation's failed-test card.

Pure: takes the runtime's error text and (when known) the evidence bundle's ``failure_kind``
and returns words a non-technical person can act on. The raw text stays on the card behind
"Technical details"; this is never a place to show it.
"""

from __future__ import annotations

import re

# Same causes the old test panel mapped (renderer WorkflowTests.tsx), first match wins.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Executable doesn't exist", re.I), "The test browser isn’t installed. Restarting Build Studio should install it."),
    (re.compile(r"Timeout \d+ms exceeded", re.I), "The page didn’t respond in time."),
    (re.compile(r"net::ERR_NAME_NOT_RESOLVED", re.I), "That website’s address couldn’t be found."),
    (re.compile(r"net::ERR_CONNECTION|net::ERR_INTERNET_DISCONNECTED", re.I), "Couldn’t connect to the website. Check your internet connection."),
    (re.compile(r"Ambiguous element resolution", re.I), "Couldn’t tell which of several matching elements to use. The page may have changed since you recorded it."),
    (re.compile(r"Element not found \(resolve miss\)", re.I), "The element this step needs wasn’t found on the page."),
    (re.compile(r"Containing frame could not be located", re.I), "The embedded section holding this element couldn’t be found. The page may have changed."),
    (re.compile(r"Element is disabled", re.I), "The element was disabled, so it couldn’t be clicked or typed into."),
    (re.compile(r"Tab not found", re.I), "The browser tab this step needs wasn’t open."),
    (re.compile(r"Verification failed:", re.I), "The step ran, but the page didn’t end up where it should have."),
    (re.compile(r"none of the candidate selectors appeared", re.I), "None of the expected results appeared on the page in time."),
    (re.compile(r"Authentication session was not captured", re.I), "Sign-in wasn’t completed or saved. Sign in again."),
    (re.compile(r"upload step has no file path", re.I), "This step needs a file to upload, but none was given."),
    (re.compile(r"Execution cancelled", re.I), "The test was stopped before it finished."),
    (re.compile(r"Runtime tool call timed out", re.I), "The test stopped responding and was cancelled."),
]

_BY_KIND = {
    "ambiguous": "Couldn’t tell which of several matching elements to use. The page may have changed since you recorded it.",
    "not_found": "The element this step needs wasn’t found on the page.",
    "entity_not_found": "The item this step works on wasn’t found on the page.",
    "frame_not_found": "The embedded section holding this element couldn’t be found. The page may have changed.",
    "verify_fail": "The step ran, but the page didn’t end up where it should have.",
}

_SMALL_WINDOW = re.compile(r"recorded at \d+\s*[x×]\s*\d+; this browser is \d+\s*[x×]\s*\d+", re.I)
_GENERIC = "Something went wrong at this step."


def summarize(message: str, failure_kind: str | None = None) -> str:
    """One plain sentence. Never returns the raw text; unmatched errors get a generic line."""
    text = message or ""
    for pattern, sentence in _PATTERNS:
        if pattern.search(text):
            break
    else:
        sentence = _BY_KIND.get(failure_kind or "", _GENERIC)
    if _SMALL_WINDOW.search(text):
        sentence += " The test window is smaller than when you recorded, so some buttons may be hidden."
    return sentence
