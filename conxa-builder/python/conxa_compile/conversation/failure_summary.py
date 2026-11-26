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
    # The runtime's own sentence (errors.js) or Playwright's raw one: the tab or the whole test browser was closed (WF-15).
    (re.compile(r"browser tab closed before the step finished|Target (page|closed)|has been closed", re.I),
     "The test browser closed while this step was running. Run the test again."),
    (re.compile(r"Timeout \d+ms exceeded", re.I), "The page didn’t respond in time."),
    (re.compile(r"net::ERR_NAME_NOT_RESOLVED", re.I), "That website’s address couldn’t be found."),
    (re.compile(r"net::ERR_CONNECTION|net::ERR_INTERNET_DISCONNECTED", re.I), "Couldn’t connect to the website. Check your internet connection."),
    (re.compile(r"Bound record matches more than one row", re.I), "More than one row on the page matches the item this step works on, so the test stopped instead of guessing which one to use."),
    (re.compile(r"Bound record could not be uniquely located", re.I), "The item this step works on isn’t on the page, so the test stopped instead of acting on a different one."),
    (re.compile(r"Ambiguous element resolution", re.I), "Couldn’t tell which of several matching elements to use. The page may have changed since you recorded it."),
    (re.compile(r"Element not found \(resolve miss\)", re.I), "The element this step needs wasn’t found on the page."),
    (re.compile(r"Containing frame could not be located", re.I), "The embedded section holding this element couldn’t be found. The page may have changed."),
    (re.compile(r"Element is disabled", re.I), "The element was disabled, so it couldn’t be clicked or typed into."),
    (re.compile(r"Tab not found", re.I), "The browser tab this step needs wasn’t open."),
    (re.compile(r"Verification failed:", re.I), "The step ran, but the page didn’t end up where it should have."),
    (re.compile(r"none of the candidate selectors appeared", re.I), "None of the expected results appeared on the page in time."),
    (re.compile(r"Authentication session was not captured", re.I), "Sign-in wasn’t completed or saved. Sign in again."),
    (re.compile(r"upload step has no file path", re.I), "This step needs a file to upload, but none was given."),
    (re.compile(r"is missing required input\(s\)|needs .+, but the value is empty", re.I),
     "This workflow needs a value that wasn’t given. Fill in every box on the test card, then run it again."),
    (re.compile(r"Skill not found", re.I),
     "Studio couldn’t find this workflow in its built copy. Choose “Build again”, then test it."),
    (re.compile(r"navigation_error|Page\.goto", re.I),
     "That start page can’t be reached. Check the address and your internet connection."),
    (re.compile(r"minimized or hidden", re.I),
     "The window showing the test browser is minimized or hidden, so the step could not be trusted. Bring Build Studio’s window back, then run the test again."),
    (re.compile(r"not showing the page at its normal size", re.I),
     "The test browser isn’t showing the page at its normal size, so the step could not be trusted. If Build Studio’s window is minimized, bring it back, then run the test again."),
    (re.compile(r"comes straight after a part the AI agent does on its own", re.I),
     "A step that can’t be undone comes straight after a part the AI agent does on its own, so the test stopped before anything ran. Put a normal step between them."),
    (re.compile(r"uses the AI agent more than \d+ times in one run", re.I),
     "This workflow uses the AI agent more than 5 times in one run, so the test stopped before the next one instead of running on. Remove or merge some of the parts the AI agent does on its own."),
    (re.compile(r"Execution cancelled", re.I), "The test was stopped before it finished."),
    (re.compile(r"Runtime tool call timed out", re.I), "The test stopped responding and was cancelled."),
]

_BY_KIND = {
    "ambiguous": "Couldn’t tell which of several matching elements to use. The page may have changed since you recorded it.",
    "not_found": "The element this step needs wasn’t found on the page.",
    "entity_not_found": "The item this step works on wasn’t found on the page.",
    "frame_not_found": "The embedded section holding this element couldn’t be found. The page may have changed.",
    "verify_fail": "The step ran, but the page didn’t end up where it should have.",
    # Deliberate stops: the run saw something it was never told how to handle and stopped on purpose.
    "decision_needed": "A popup showed up that asks a question this workflow has no answer for, so the test stopped instead of guessing.",
    "left_open_unclassified": "A panel was still open at the end and the test couldn’t tell whether it is just news or a question.",
    "couldnt_confirm": "The step ran, but nothing on the page confirmed it finished, so the test stopped instead of reporting success. Check it before running it again.",
    "bad_input": "One of the values given for this test isn’t one this step can use.",
    "tab_not_found": "The browser tab this step needs wasn’t open.",
    # The test never started: Studio could not get the workflow ready to test. Not a result about the workflow.
    "package_build": "Studio couldn’t get this workflow ready to test, so nothing was run. Choose “Build again”, then test it.",
}
# The kinds whose cause is already known exactly: the kind decides the sentence, not a word in the message.
_KIND_FIRST = frozenset({"decision_needed", "couldnt_confirm", "left_open_unclassified", "bad_input", "tab_not_found", "package_build"})

# A download step that got no usable file. The runtime marks it bad input only to skip recovery, so the reason
# comes from its words, not the kind ("One of the values given..." named the wrong cause, WF-14). First match wins.
_DOWNLOAD = re.compile(r"Expected a file download at this step", re.I)
_DOWNLOAD_REASONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"empty or incomplete", re.I), "The file downloaded empty or cut short, so the test stopped instead of using it."),
    (re.compile(r"did not finish downloading within", re.I), "The file took too long to download, so the test stopped waiting for it."),
    (re.compile(r"was cancelled|was interrupted", re.I), "The download stopped before the file was saved."),
    (re.compile(r"none arrived within", re.I), "No file started downloading at this step."),
]
_DOWNLOAD_OTHER = "The file couldn’t be downloaded."
# A read step whose element was there but empty. Also marked bad input only to skip recovery (BUILD-107 (3)).
_EMPTY_VALUE = re.compile(r"holds no value to extract", re.I)
_EMPTY_VALUE_SENTENCE = "The value this step reads was empty on the page, so the test stopped instead of using nothing."

# A part the AI agent did on its own ended without finishing (runtime server.js: "Agent span at step N ended (<code>): ...").
# The reason is known exactly, so it is said, not hidden behind the generic line (WF-13).
_SPAN_ENDED = re.compile(r"Agent span at step \d+ ended \((\w+)\):\s*(.*)", re.I | re.S)
_SPAN_REASONS = {
    "exhausted": "The AI agent used up the actions or time it was given for this part before finishing it.",
    "refused_too_often": "The AI agent kept trying things this part doesn’t allow, so it was stopped.",
    "answer_invalid": "The AI agent never gave an answer in the form this part asks for.",
    "page_closed": "The page closed while the AI agent was working on this part.",
}

_SMALL_WINDOW = re.compile(r"recorded at \d+\s*[x×]\s*\d+; this browser is \d+\s*[x×]\s*\d+", re.I)
_GENERIC = "Something went wrong at this step."


def _after_failed_prefix(text: str) -> str:
    """The sentence after "Step N (type) failed: ", cut before a technical bracket."""
    m = re.search(r"\bfailed:\s*(.+?)(?:\s*\(|\n|$)", text)
    return m.group(1).strip()[:300] if m else ""


def _which_ones(candidates: list[str] | None) -> str:
    """The names a tie was between, so the person knows what to look at. Chat cannot re-point a step: Human Edit does."""
    names = [n.strip() for n in (candidates or []) if n and n.strip()][:3]
    if len(names) < 2:
        return ""
    return " It matched " + ", ".join(f"“{n}”" for n in names) + ". Open this step in Human Edit to pick the right one."


def summarize(message: str, failure_kind: str | None = None, candidates: list[str] | None = None) -> str:
    """One plain sentence. Never returns the raw text; unmatched errors get a generic line."""
    text = message or ""
    span = _SPAN_ENDED.search(text)
    if span:
        first = (span.group(2).strip().splitlines() or [""])[0]
        why = re.sub(r"^The agent gave up:?\s*", "", first).strip().rstrip(".")[:200]
        gave_up = "The AI agent gave up on this part" + (f": {why}." if why else ".")
        sentence = gave_up if span.group(1).lower() == "gave_up" else _SPAN_REASONS.get(span.group(1).lower(), "The AI agent couldn’t finish this part.")
    elif _DOWNLOAD.search(text):
        sentence = next((s for p, s in _DOWNLOAD_REASONS if p.search(text)), _DOWNLOAD_OTHER)
    elif _EMPTY_VALUE.search(text):
        sentence = _EMPTY_VALUE_SENTENCE
    elif failure_kind in _KIND_FIRST:
        sentence = _BY_KIND[failure_kind]
    elif (failure_kind or "").startswith("fault:"):
        # The app's own refusal. The runtime wrote a plain sentence for it ("This step didn't finish: ..."): keep it.
        sentence = _after_failed_prefix(text) or "The app reported a problem, so the test stopped."
    else:
        for pattern, sentence in _PATTERNS:  # noqa: B007 — `sentence` is read after the loop breaks
            if pattern.search(text):
                break
        else:
            sentence = _BY_KIND.get(failure_kind or "", _GENERIC)
    if failure_kind == "ambiguous" or re.search(r"Ambiguous element resolution", text, re.I):
        sentence += _which_ones(candidates)
    if _SMALL_WINDOW.search(text):
        sentence += " The test window is smaller than when you recorded, so some buttons may be hidden."
    return sentence
