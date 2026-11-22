"""Deterministic selector quality gates (shared by ranking + stable selector build)."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from conxa_compile import signin_names

if TYPE_CHECKING:
    from conxa_core.models.skill_spec import IdentitySignal


def is_dynamic_id(selector: str) -> bool:
    if not selector.startswith("#"):
        return False
    value = selector[1:]
    if not value:
        return True
    if value.startswith("_r_"):
        return True
    # Ids a framework hands out per render: React's useId (":r1:", ":rh"), Google's ":tv", Ember/React/Angular counters.
    # They change on the next load, so an element is not "the one with this id".
    if value.lstrip("\\").startswith(":") or re.match(r"^(?:ember|react|vue|ng|mat|cdk)[-_]?\d+", value, re.I):
        return True
    if re.search(r"[:][a-z0-9]{6,}", value, re.I):
        return True
    if re.search(r"-[a-f0-9]{8,}\b", value, re.I):
        return True
    return bool(re.search(r"\d", value) and re.search(r"[_\-]", value))


def is_unstable_generated_css_fragment(selector: str) -> bool:
    """Heuristic for hashed / scoped class segments in attribute selectors (not full parser)."""
    s = selector.strip()
    if len(s) < 24:
        return False
    if re.search(r"\[[^\]]{80,}\]", s):
        return True
    if s.count("[class") >= 2 and re.search(r"[a-f0-9]{6,}", s, re.I):
        return True
    return False


_INVALID_ARIA_ROLE_TOKENS = frozenset(
    {
        "path",
        "svg",
        "g",
        "div",
        "span",
        "none",
        "presentation",
        "input",
    }
)


def is_invalid_aria_semantic_role(selector: str) -> bool:
    """Reject selectors that claim impossible / non-interactive ARIA roles."""
    m = re.search(r'\[role=["\']([^"\']+)["\']', selector, re.I)
    if not m:
        return False
    r = str(m.group(1) or "").strip().lower()
    return r in _INVALID_ARIA_ROLE_TOKENS


def is_valid_selector(selector: str) -> bool:
    if not selector:
        return False
    normalized = selector.strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    if lowered in {'[role="input"]', "[role='input']"}:
        return False
    if lowered.startswith("[role=") and "input" in lowered:
        return False
    if is_invalid_aria_semantic_role(normalized):
        return False
    return True


def is_weak_literal_token(selector: str) -> bool:
    """Reject bare words mistaken for selectors (e.g. field name tokens)."""
    s = selector.strip()
    if not s:
        return True
    lowered = s.lower()
    if lowered.startswith(("[", "#", ".", "/", "*", "(")):
        return False
    for prefix in ("input", "button", "textarea", "select", "form", "option", "label", "svg", "path", "a[", "a."):
        if lowered.startswith(prefix):
            return False
    if "text=" in lowered:
        return False
    if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", s) and len(s) < 48:
        return True
    return False


def is_brittle_deep_chain(selector: str, *, max_xpath_segments: int = 10, max_css_child_depth: int = 6) -> bool:
    """Heuristic: very long positional CSS or XPath trees are fragile."""
    s = selector.strip()
    if not s:
        return False
    if s.startswith("/"):
        parts = [p for p in re.split(r"/+", s) if p and p not in (".",)]
        depth = len(parts)
        return depth > max_xpath_segments
    if ">" in s:
        depth = s.count(">") + 1
        return depth > max_css_child_depth
    if len(s) > 220:
        return True
    return False


_POSITIONAL_RE = re.compile(r":nth-(?:of-type|child)\(", re.I)
# A segment carries real identity when it names the element by class / id / attribute rather
# than by its position among siblings. `select.react-datepicker__year-select` qualifies;
# `div:nth-of-type(2)` does not.
_IDENTIFYING_SEG_RE = re.compile(r"[.#\[]")


def salvage_deep_css_tail(selector: str, *, max_css_child_depth: int = 6) -> str:
    """Shortest identifying tail of an over-deep CSS chain, or "" when nothing is salvageable.

    `is_brittle_deep_chain` is right that a long positional chain is fragile — but discarding it
    outright also throws away the element's only durable identity when that identity sits in the
    LAST segment. react-datepicker's year <select> recorded as an 8-deep chain ending in
    `select.react-datepicker__year-select`: the head is positional noise, the tail is a unique,
    semantic, non-positional selector. The whole thing was being blanked to "", leaving that step
    with no matchable signal at all.

    Returns the shortest tail (fewest segments) that is free of positional pseudo-classes, names
    the element by class/id/attribute, and passes the ordinary quality gates.
    """
    if ">" not in selector:
        return ""
    segments = [s.strip() for s in selector.split(">")]
    segments = [s for s in segments if s]
    if len(segments) <= max_css_child_depth:
        return ""
    if not segments or _POSITIONAL_RE.search(segments[-1]):
        return ""
    if not _IDENTIFYING_SEG_RE.search(segments[-1]):
        return ""
    # Grow the tail from the right, stopping at the first positional segment — a positional
    # ancestor re-introduces exactly the fragility the depth gate exists to reject.
    for take in range(1, min(len(segments), max_css_child_depth) + 1):
        tail_segments = segments[-take:]
        if take > 1 and _POSITIONAL_RE.search(tail_segments[0]):
            break
        candidate = " > ".join(tail_segments)
        if selector_passes_filters(candidate):
            return candidate
    return ""


def _strip_invalid_role_fragments(selector: str) -> str:
    """Remove [role="<invalid>"] fragments from compound aria selectors.

    '[role="input"][name="Search services"]' → '[name="Search services"]'
    Lets useful name/testid attributes survive a bad role token from bridge.js.
    """
    return re.sub(
        r'\[role=["\']([^"\']+)["\']\]',
        lambda m: "" if m.group(1).strip().lower() in _INVALID_ARIA_ROLE_TOKENS else m.group(0),
        selector,
    ).strip()


def filter_selectors_dict(selectors: dict[str, Any] | None) -> dict[str, Any]:
    """Strip selector channels that fail quality gates before persisting to skill steps."""
    if not isinstance(selectors, dict):
        return {}
    out: dict[str, Any] = dict(selectors)
    for key in ("css", "aria", "text_based", "xpath", "name"):
        raw = out.get(key)
        if not isinstance(raw, str):
            continue
        s = raw.strip()
        if not s:
            out[key] = ""
            continue
        if selector_passes_filters(s):
            out[key] = s
            continue
        # For aria selectors with an invalid role token, try salvaging the rest
        if key == "aria" and is_invalid_aria_semantic_role(s):
            salvaged = _strip_invalid_role_fragments(s)
            if salvaged and selector_passes_filters(salvaged):
                out[key] = salvaged
                continue
        # An over-deep CSS chain often carries the element's only durable identity in its last
        # segment. Keep that tail rather than dropping the channel to "" and leaving the step
        # with nothing matchable.
        if key == "css":
            tail = salvage_deep_css_tail(s)
            if tail:
                out[key] = tail
                continue
        out[key] = ""
    return out


def selector_passes_filters(selector: str) -> bool:
    if not is_valid_selector(selector):
        return False
    if is_weak_literal_token(selector):
        return False
    if is_dynamic_id(selector):
        return False
    if is_unstable_generated_css_fragment(selector):
        return False
    if is_brittle_deep_chain(selector):
        return False
    return True


def prefilter_selector_candidate(selector: str) -> bool:
    """Gate before scoring: same as selector_passes_filters (explicit alias for ranking pipelines)."""
    return selector_passes_filters(selector)


# ---------------------------------------------------------------------------
# Phase 2 compile-time gates (Final Selector Architecture)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# Mail domains reserved for documentation and testing (RFC 2606, RFC 6761). No mailbox can exist there, so an
# address on one is sample text a page's author wrote (a placeholder like "name@example.com"), never an account.
_SAMPLE_MAIL_DOMAINS = ("example.com", "example.net", "example.org")
_SAMPLE_MAIL_TLDS = (".example", ".test", ".invalid", ".localhost")


def has_personal_email(text: Any) -> bool:
    """True when the text carries an email address that can belong to someone. The one rule both the compile-time
    scrub (drop_personal_target_text) and the build-time check (skill_package_builder) use, so whatever the
    compiler keeps, the build accepts."""
    for m in _EMAIL_RE.finditer(str(text or "")):
        domain = m.group().rsplit("@", 1)[-1].lower()
        if not (domain.endswith(_SAMPLE_MAIL_TLDS) or any(domain == d or domain.endswith("." + d) for d in _SAMPLE_MAIL_DOMAINS)):
            return True
    return False


def _without_personal_email(value: Any) -> Any:
    """The value with every string that carries a personal email blanked, and dropped from lists."""
    if isinstance(value, str):
        return "" if has_personal_email(value) else value
    if isinstance(value, list):
        return [_without_personal_email(v) for v in value if not (isinstance(v, str) and has_personal_email(v))]
    if isinstance(value, dict):
        return {k: _without_personal_email(v) for k, v in value.items()}
    return value


def scrub_email(text: str) -> str:
    """Text a person reads in chat, with any email address cut out (an account button's label
    carries the signed-in address)."""
    return " ".join(_EMAIL_RE.sub(" ", str(text or "")).split())


# A form label longer than this wrapped several elements (an account chip: "Google Account" + a name + an email).
_MAX_LABEL_TEXT = 60


def without_sign_in_names(value: Any, names: list[tuple[str, frozenset[str]]]) -> Any:
    """The value with every string that has a user name typed in a sign-in window in it (signin_names.mentions)
    blanked, and dropped from lists."""
    if isinstance(value, str):
        return "" if signin_names.mentions(value, names) else value
    if isinstance(value, list):
        return [without_sign_in_names(v, names) for v in value
                if not (isinstance(v, str) and signin_names.mentions(v, names))]
    if isinstance(value, dict):
        return {k: without_sign_in_names(v, names) for k, v in value.items()}
    return value


def drop_personal_target_text(
    events: list[dict[str, Any]], names: list[tuple[str, frozenset[str]]] | None = None,
) -> list[dict[str, Any]]:
    """Events with the text the person's own account put on the page taken out of everything that describes the
    element (its target, the selectors the recorder built for it, and its surroundings), once, before any selector,
    fingerprint or binding is built from it. Whatever is built later cannot carry what is not there.
    A text with a personal email address in it (has_personal_email), or a label too long to be one label, is
    blanked; so is surrounding text with a user name typed in a sign-in window (`names`, read from this machine's
    groups when None). Nothing else changes. The input events are not modified."""
    if names is None:
        from conxa_core.config import settings  # noqa: PLC0415

        # shortcut: every group's names, not only this workflow's group (the compile does not know its group); a
        # common user name ("admin") also drops matching surroundings elsewhere. Pass the group down if that bites.
        names = signin_names.load(settings.data_dir)
    out: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            out.append(ev)
            continue
        # A page's title can carry the signed-in address too (Gmail: "Inbox (274) - name@gmail.com - Gmail").
        # It is saved on every step as context, so cut the address out here, once, like the target text.
        page = ev.get("page")
        if isinstance(page, dict) and isinstance(page.get("title"), str) and _EMAIL_RE.search(page["title"]):
            ev = {**ev, "page": {**page, "title": scrub_email(page["title"])}}
        target = ev.get("target")
        if isinstance(target, dict):
            target = _without_personal_email(target)
            if isinstance(target.get("label_text"), str) and len(target["label_text"]) > _MAX_LABEL_TEXT:
                target["label_text"] = ""
            if target != ev["target"]:
                ev = {**ev, "target": target}
        for key in ("selectors", "context"):
            if isinstance(ev.get(key), dict):
                cleaned = _without_personal_email(ev[key])
                if key == "context":
                    # Only the surroundings: a target or selector named "Admin" is the app's, kept for the step.
                    cleaned = without_sign_in_names(cleaned, names)
                if cleaned != ev[key]:
                    ev = {**ev, key: cleaned}
        out.append(ev)
    return out
_METACHAR_RE = re.compile(r"[>+~|^$]")  # CSS combinators / unsanitisable metacharacters


_TESTID_SEL_RE = re.compile(r'^internal:testid=\[([\w-]+)=["\']?([^"\']+)["\']?\]$')
_TEXT_SEL_RE = re.compile(r'^internal:text="(.*)"$', re.DOTALL)
_ROLE_SEL_RE = re.compile(r'^internal:role=([a-zA-Z]+)(?:\[name="([^"]*)"\])?$')


# A compile pass calls _count_css/_count_text once per identity-signal candidate, and
# consecutive recorded events on the same page share the same dom_html snapshot — cache
# the parse so repeat calls against identical HTML don't re-run BeautifulSoup/lxml.
@lru_cache(maxsize=8)
def _parse_soup(html: str) -> Any:
    from bs4 import BeautifulSoup  # type: ignore
    return BeautifulSoup(html, "lxml")


def _count_css(html: str, css_selector: str) -> int:
    soup = _parse_soup(html)  # bs4/lxml are hard dependencies — ImportError propagates
    try:
        return len(soup.select(css_selector))
    except Exception:  # noqa: BLE001 — Playwright-only CSS extensions (e.g. :has-text()) that
        # a real element's structural selector can legitimately carry; soupsieve can't evaluate
        # them, so "can't verify statically" correctly allows the selector through.
        return 1


def _count_text(html: str, text: str) -> int:
    target = text.strip().lower()
    if not target:
        return 1
    soup = _parse_soup(html)  # bs4/lxml are hard dependencies — ImportError propagates
    return sum(
        1 for el in soup.find_all(True)
        if not el.find(True) and el.get_text(strip=True).lower() == target
    )


_ARIA_SNAPSHOT_LINE_RE = re.compile(r'^\s*-\s+([a-zA-Z][a-zA-Z0-9]*)(?:\s+"((?:[^"\\]|\\.)*)")?')


def _count_role(a11y_tree: dict[str, Any], role: str, name: str | None) -> int:
    """Count nodes in the Playwright accessibility snapshot matching role (+ name).

    ARIA role isn't derivable from raw HTML alone, so this walks the recorded
    a11y evidence (the browser's own computed roles) rather than the HTML. The
    recorder captures `{"aria_snapshot": "<yaml>"}` — `Locator.aria_snapshot()`,
    one `- role "name":` line per node.
    """
    wanted_role = role.strip().lower()
    wanted_name = (name or "").strip().lower()
    count = 0

    yaml_text = a11y_tree.get("aria_snapshot") if isinstance(a11y_tree, dict) else None
    if not isinstance(yaml_text, str):
        return count
    for line in yaml_text.splitlines():
        m = _ARIA_SNAPSHOT_LINE_RE.match(line)
        if not m:
            continue
        if m.group(1).strip().lower() != wanted_role:
            continue
        node_name = (m.group(2) or "").strip().lower()
        # Playwright getByRole({name}) is a case-insensitive substring of the
        # accessible name, not an exact string. Exact equality here dropped a
        # real menuitem whose recorded name still had a keyboard-shortcut tail
        # ("File upload Alt+C then U") while the snapshot said "File upload".
        if not wanted_name or wanted_name in node_name:
            count += 1
    return count


def _count_selector_matches(
    selector: str, dom_html: str, a11y_tree: dict[str, Any] | None
) -> int:
    """Count matches for a compiled (internal: grammar or raw CSS) selector against the
    recorded page. Relational (`>> right-of=...`) selectors are spatially disambiguated
    by the runtime resolver, not by static counting here — treated as unique.
    """
    selector = selector.strip()
    if not selector:
        return 0

    m = _TESTID_SEL_RE.match(selector)
    if m:
        return _count_css(dom_html, f'[{m.group(1)}="{m.group(2)}"]')

    m = _TEXT_SEL_RE.match(selector)
    if m:
        return _count_text(dom_html, m.group(1))

    m = _ROLE_SEL_RE.match(selector)
    if m:
        if a11y_tree is None:
            return 1  # can't verify role without the a11y tree — allow through
        return _count_role(a11y_tree, m.group(1), m.group(2))

    if selector.startswith("internal:") or ">>" in selector:
        return 1  # relational / composite — not counted here

    return _count_css(dom_html, selector)  # raw css / css-id fallback


def uniqueness_gate(
    selector: str,
    dom_html: str | None,
    a11y_tree: dict[str, Any] | None = None,
    absent_ok: bool = False,
) -> bool:
    """Return True if selector matches exactly 1 node in the recorded page.

    `dom_html` is the recorded page's raw HTML (conxa_core.storage.snapshots.read_dom_snapshot);
    `a11y_tree` is the Playwright accessibility snapshot (read_a11y_snapshot), needed to count
    internal:role= matches since ARIA role isn't derivable from raw HTML.
    Returns True when no HTML snapshot is available (can't verify, allow through).

    `absent_ok`: when True, a 0-match result is also treated as "can't verify, allow through"
    rather than "not unique". A 0 count here is genuinely ambiguous — it can mean the element
    truly isn't on the recorded page (a real miss), or that the recorded snapshot just didn't
    contain it (e.g. it was captured before a modal/portal-mounted element existed — see
    conxa_compile.recorder.bridge.js's interactiveSignature dedup signature, which can miss
    elements past its cap). Compile-time identity signal generation passes True here so a
    snapshot gap doesn't wrongly stamp a real, durable selector as unverified; the fallback-pool
    dedup in build.py keeps the strict default, since a *structural* selector that matches
    nothing recorded is legitimately worth dropping there.
    """
    if not dom_html:
        return True
    count = _count_selector_matches(selector, dom_html, a11y_tree)
    if count == 0 and absent_ok:
        return True
    return count == 1


def resolves_to_nothing(
    selector: str, dom_html: str | None, a11y_tree: dict[str, Any] | None = None
) -> bool:
    """True when the selector provably matches no node in the recorded page.

    Distinct from `uniqueness_gate(absent_ok=True)`, which folds "matched nothing" into
    "couldn't verify". For a role+name selector that conflation is wrong: the name is derived
    from the element's OWN recorded attributes, so a 0 count against a real a11y snapshot means
    the name is fabricated, not that the snapshot was incomplete. `_count_selector_matches`
    already returns 1 for a role selector when the a11y tree is missing, so a genuine
    "can't verify" never reaches here as a 0.
    """
    if not dom_html:
        return False
    return _count_selector_matches(selector, dom_html, a11y_tree) == 0


def dedup_by_orthogonality(signals: list["IdentitySignal"]) -> list["IdentitySignal"]:
    """Keep the highest-durability signal per orthogonality class; drop the rest. A signal known to match several
    elements never takes its class's slot from one that picks this element: both stay, in durability order, so the
    resolver tries the other when the first ties (WF-4 Leg A: three boxes shared `input.oxd-input`, and the unique
    path was dropped)."""
    best: dict[str, "IdentitySignal"] = {}
    best_unique: dict[str, "IdentitySignal"] = {}
    for sig in signals:
        oc = sig.orthogonality_class
        if oc not in best or sig.durability > best[oc].durability:
            best[oc] = sig
        if sig.unique_at_compile and (oc not in best_unique or sig.durability > best_unique[oc].durability):
            best_unique[oc] = sig
    # Return in original durability-descending order
    kept = {id(s) for s in best.values()} | {id(s) for s in best_unique.values()}
    return [s for s in signals if id(s) in kept]


# What a control is doing right now, written into its class: a whole token ("active", "is-open") or a modifier
# ("oxd-input--focus", "Mui-focused", "nav-item-active", "is-open"). Not "is-primary": a variant, not a state. Unique while the person types, absent on a fresh page.
_STATE_WORDS = ("focus|focused|focus-visible|focus-within|hover|hovered|active|selected|disabled|expanded|collapsed|"
                "open|opened|closed|checked|pressed|loading|dragging|current|visited|invalid|valid|dirty|touched|pristine|"
                "error|filled|has-value|empty")
_STATE_CLASS_RE = re.compile(
    rf"^(?:(?:is|has|state)-)?(?:{_STATE_WORDS})$|--(?:{_STATE_WORDS})$|[-_](?:focus|focused|hover|hovered|active|selected|"
    r"checked|pressed|expanded|disabled)$", re.I)
_CLASS_TOKEN_RE = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")


def drop_state_classes(css: str) -> str:
    """``css`` without class tokens that name a passing state (see _STATE_CLASS_RE). A segment left with no class keeps
    its tag. Mirrors bridge.js::isStateClass, which leaves them out of new recordings."""
    if not css or "." not in css:
        return css
    return _CLASS_TOKEN_RE.sub(lambda m: "" if _STATE_CLASS_RE.search(m.group(1)) else m.group(0), css)


def pii_bind(
    selector: str, inputs: dict[str, Any] | None = None
) -> tuple[str, bool]:
    """Replace PII literals in selector with {{var}} references.

    Returns (modified_selector, was_bound). If un-escapable metacharacters remain
    after binding, returns ("", True) to signal the selector must be dropped.
    """
    modified = selector
    bound = False

    # Replace email addresses
    if _EMAIL_RE.search(modified):
        modified = _EMAIL_RE.sub("{{email}}", modified)
        bound = True

    # Replace input literal values found verbatim in selector
    if inputs:
        for key, val in inputs.items():
            if not val or not isinstance(val, str) or len(val) < 4:
                continue
            if val in modified:
                modified = modified.replace(val, f"{{{{{key}}}}}")
                bound = True

    if bound and _METACHAR_RE.search(modified.replace("{{", "").replace("}}", "")):
        return ("", True)

    return (modified, bound)


def xpath_shadow_guard(engine: str, shadow_path: list[Any] | None) -> bool:
    """Return False (block) if engine is xpath and shadow_path is non-empty.

    XPath cannot cross shadow roots; such selectors must be dropped.
    """
    if not shadow_path:
        return True
    return engine != "xpath"


def css_shadow_unverifiable(engine: str, shadow_path: list[Any] | None) -> bool:
    """True if a css-structural/css-id signal's unique_at_compile stamp cannot be trusted.

    uniqueness_gate's dom_html is a standard HTML serialization of the recorded snapshot, which
    never includes open-shadow-root internals — a shadow-sourced CSS selector always counts 0
    matches there regardless of how many real matches exist on the live page, and identity_bundle
    calls uniqueness_gate with absent_ok=True, so that 0 gets stamped unique_at_compile=True
    instead of being flagged as unverifiable. Unlike xpath these selectors aren't dropped outright
    (role/text signals from the same element usually outrank them once bridge.js's shadow-host
    accessible-name fallback is populated), but the stamp itself must be corrected post hoc.
    """
    return bool(shadow_path) and engine in ("css-structural", "css-id")


# ---------------------------------------------------------------------------
# Ephemeral-anchor filter (used by spatial-anchor / relational signal builders)
# ---------------------------------------------------------------------------

# Matched as whole words or phrases (a trailing "s" allowed), never as substrings, and only words
# that name an overlay itself. Bare "notification" and "subscribe" are left out: they are also
# ordinary labels ("Notification settings", "Subscribe to updates") and made real anchors vanish.
# English only; a structural test (overlay role, fixed position) needs the recorder to tag anchors.
_EPHEMERAL_ANCHOR_KEYWORDS = frozenset({
    "cookie",
    "consent",
    "gdpr",
    "ccpa",
    "banner",
    "popup",
    "pop-up",
    "pop up",
    "newsletter",
    "toast",
    "snackbar",
    "notification bar",
    "notification banner",
    "we use cookies",
    "accept all",
    "manage preferences",
})
_EPHEMERAL_ANCHOR_RE = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(k) for k in sorted(_EPHEMERAL_ANCHOR_KEYWORDS, key=len, reverse=True)) + r")s?(?![a-z0-9])"
)


def is_ephemeral_anchor(phrase: str) -> bool:
    """Return True if an anchor phrase names a transient overlay unsuitable as a durable
    spatial anchor (cookie/consent banners, popups, newsletter prompts, notification toasts).

    These elements are typically absent, dismissed, or repositioned at replay time, making
    them the worst-possible reference points for relational selectors.
    """
    lowered = phrase.strip().lower()
    return bool(_EPHEMERAL_ANCHOR_RE.search(lowered))


# Bare HTML tag names that leak into anchor_phrases when the recorder captures a container
# node rather than meaningful visible text. These are worthless as spatial landmarks.
_STRUCTURAL_TAG_TOKENS = frozenset({
    "a", "aside", "button", "div", "footer", "form", "g",
    "header", "img", "input", "label", "li", "main", "nav",
    "ol", "option", "p", "path", "section", "select", "span",
    "svg", "table", "td", "textarea", "th", "tr", "ul",
})


def is_low_quality_anchor(phrase: str) -> bool:
    """Return True if an anchor phrase is unusable as a durable spatial reference.

    Covers:
    - Ephemeral overlays (cookie/consent/banner — delegates to is_ephemeral_anchor).
    - Bare HTML tag tokens that the recorder leaks when it captures container nodes
      instead of meaningful visible text (e.g. "div", "svg:", "button:").
    - Strings that are too short to be meaningful landmarks (< 2 chars after stripping).
    - Concatenated container text: more than 6 whitespace-separated tokens OR more than
      48 characters (catches whole-header nav blobs like
      "M My Workspace Projects Search CTRL + K K New Upgrade K").
    - Stray single-letter prefix (icon/avatar initial concatenated with adjacent text):
      first whitespace token is a single alphanumeric character AND the phrase has ≥3
      tokens (catches "M My Workspace"; preserves 2-token "A Records").
    """
    token = phrase.strip().lower().rstrip(":")
    if not token or len(token) < 2:
        return True
    if token in _STRUCTURAL_TAG_TOKENS:
        return True
    if is_ephemeral_anchor(phrase):
        return True
    # --- concatenated container text ---
    stripped = phrase.strip()
    parts = stripped.split()
    if len(parts) > 6 or len(stripped) > 48:
        return True
    # --- stray single-letter prefix (icon/avatar initial) ---
    if len(parts) >= 3 and len(parts[0]) == 1 and parts[0].isalnum():
        return True
    return False


if __name__ == "__main__":
    html = '<div><button id="go">Go</button><span>Other</span></div>'
    _parse_soup.cache_clear()
    assert _count_css(html, "#go") == 1
    assert _count_text(html, "go") == 1
    hits_before = _parse_soup.cache_info().hits
    _count_css(html, "#go")  # same html string -> should hit the cache, not re-parse
    assert _parse_soup.cache_info().hits == hits_before + 1
    print("ok")
