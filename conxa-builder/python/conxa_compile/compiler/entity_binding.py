"""PROD-3 entity binding: scope a step's target to the specific row/record it must act on.

Detects, for a step whose target sits inside a repeating list/table row, a Playwright selector
matching every sibling row plus the identifying text that picks out THIS run's row —
runtime/app/resolution.js::entityRoots narrows resolution to it before any signal is scored, so
recovery can never substitute a same-looking element from a different row.

Deterministic only, built from the ancestor chain and outer_html the recorder already captures
(bridge.js::captureAncestors) — no LLM involvement, matching the primary-compile-path invariant
that the LLM never writes selectors (CLAUDE.md Key Invariants).
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from conxa_compile.compiler.input_binding import derive_input_binding
from conxa_compile.compiler.selector_filters import is_dynamic_id, is_low_quality_anchor
from conxa_compile.compiler.stable_hash import strip_dynamic_classes
from conxa_core.models.skill_spec import EntityBinding

Step = dict[str, Any]

_MIN_SIBLINGS = 2
_MAX_IDENTIFIER_LEN = 80
# How many ancestor levels above the target to search for a "row". A genuine repeating-record
# row (a <tr>, a card <li>, a list item div) wraps its actionable control tightly — the target
# sits directly inside it or one level deeper. Walking further up hits page-layout chrome
# instead: grid-framework utility classes like Foundation/Bootstrap's ubiquitous ".row" (or
# Tailwind's ".grid") repeat all over unrelated sections of nearly every page for pure layout,
# with no relation to data records. Unbounded, that made detect_entity_binding treat something
# like a login page's `<body> > div.row` (one row is #flash-messages, the other is the whole
# page content) as a 2-row "list", then pick a fragment of the page's own help text as the
# record identifier — corrupting an ordinary Login button into a phantom record lookup.
_MAX_ROW_SEARCH_DEPTH = 3

# Text that repeats identically on every row (button/control labels) and therefore identifies
# nothing. Merged with the destructive-intent vocabulary so a Delete button's own label is never
# mistaken for the row's identity.
_NON_IDENTIFYING_TOKENS = frozenset({
    "delete", "remove", "revoke", "destroy", "purge", "cancel", "cancel_subscription",
    "edit", "select", "view", "download", "add", "buy", "checkout", "submit", "confirm",
    "qty", "quantity", "x", "×",
})


def _css_class_selector(classes: list[str]) -> str:
    return "".join(f".{c}" for c in classes[:3] if re.fullmatch(r"[A-Za-z_-][\w-]*", c))


def _direct_child_count(parent_outer_html: str, tag: str, class_set: set[str]) -> int:
    if not parent_outer_html or not tag:
        return 0
    try:
        soup = BeautifulSoup(parent_outer_html, "html.parser")
    except Exception:
        return 0
    root = soup.find(True)
    if root is None:
        return 0
    count = 0
    for child in root.find_all(tag, recursive=False):
        raw_classes = child.get("class") or []
        if isinstance(raw_classes, str):
            raw_classes = raw_classes.split()
        child_classes = set(strip_dynamic_classes([str(c) for c in raw_classes]))
        if class_set and child_classes != class_set:
            continue
        count += 1
    return count


def _build_container_selector(parent: dict[str, Any], row_tag: str, row_class_suffix: str) -> str:
    row_part = f"{row_tag}{row_class_suffix}"
    parent_id = str(parent.get("id") or "").strip()
    if parent_id and not is_dynamic_id(f"#{parent_id}"):
        return f"#{parent_id} > {row_part}"
    parent_tag = str(parent.get("tag") or "").strip().lower()
    parent_class_suffix = _css_class_selector(strip_dynamic_classes(list(parent.get("classes") or [])))
    if parent_tag and parent_class_suffix:
        return f"{parent_tag}{parent_class_suffix} > {row_part}"
    if parent_tag:
        return f"{parent_tag} > {row_part}"
    return row_part


def _row_text_segments(row_outer_html: str) -> list[str]:
    if not row_outer_html:
        return []
    try:
        soup = BeautifulSoup(row_outer_html, "html.parser")
    except Exception:
        return []
    root = soup.find(True)
    if root is None:
        return []
    raw = root.get_text(separator="␟", strip=True)
    return [s.strip() for s in raw.split("␟") if s.strip()]


def _pick_identifier(row: dict[str, Any]) -> str:
    candidates = []
    for seg in _row_text_segments(str(row.get("outer_html") or "")):
        if not seg or len(seg) > _MAX_IDENTIFIER_LEN:
            continue
        if seg.lower() in _NON_IDENTIFYING_TOKENS:
            continue
        if is_low_quality_anchor(seg):
            continue
        candidates.append(seg)
    if not candidates:
        return ""
    # A segment carrying a digit run (invoice/order/SKU number) is the most durable identity —
    # prefer it over a plain name, which can repeat (e.g. two rows for the same product).
    with_digits = [c for c in candidates if re.search(r"\d", c)]
    pool = with_digits or candidates
    return max(pool, key=len)


# --- is the target one item picked from a list? ----------------------------------------------
# Controls act on the page; the other things a person clicks (a link, an option, a cell) name an
# item. A control inside a row (a per-row "Edit") is still a control.
_CONTROL_ROLES = frozenset({
    "button", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "switch", "combobox",
    "textbox", "input", "select", "textarea", "checkbox", "radio",
})
# Data rows. Real row elements count outright. A <div>/<section>/<article> grid proves nothing on its
# own (layout repeats), so it counts only when it says it is a row: an ARIA row-like role, or a class
# or id that names one (react-directory-row, result-card, item-3). Bootstrap's bare ".row" is layout.
_DATA_ROW_TAGS = frozenset({"tr", "li"})
_GRID_ROW_TAGS = frozenset({"div", "section", "article"})
_ROW_ROLES = frozenset({"row", "listitem", "option", "treeitem"})
_ROW_NAME_RE = re.compile(r"(?:^|[-_ ])(?:row|item|entry|card|record|result)s?(?:$|[-_ 0-9])", re.IGNORECASE)
_ROLE_ATTR_RE = re.compile(r"""^\s*<[^>]*?\brole\s*=\s*["']([a-z]+)["']""", re.IGNORECASE)


def _names_a_data_row(ancestor: dict[str, Any], tag: str) -> bool:
    if tag in _DATA_ROW_TAGS:
        return True
    if tag not in _GRID_ROW_TAGS:
        return False
    role = _ROLE_ATTR_RE.match(str(ancestor.get("outer_html") or ""))
    if role and role.group(1).lower() in _ROW_ROLES:
        return True
    names = [str(c) for c in (ancestor.get("classes") or []) if str(c) != "row"] + [str(ancestor.get("id") or "")]
    return any(_ROW_NAME_RE.search(n) for n in names)
# A header row is chrome, not a record.
_HEADER_PARENTS = frozenset({"thead"})
_PEER_SEARCH_DEPTH = 10
# bridge.js::captureAncestors cuts every ancestor's outer_html at this many characters. A parent that
# long has more children than were kept, so counting the rows left in it undercounts.
_ANCESTOR_HTML_CAP = 2000


def repeating_peers(ev: Step) -> int:
    """How many like items the clicked target was one of: >0 when it is an item named in a list or
    table, 0 for a control or for anything not in a row.

    That is the structural difference between *choosing one of many* (a file in a list, a record in
    a table: a value that very likely changes each run) and *pressing a control* (a toolbar or dialog
    button: the app's own chrome). Deterministic, from the ancestor chain the recorder already
    captures, no text and no model. Looks further up than ``detect_entity_binding`` because it only
    needs to know that a row exists, not to build a selector for it."""
    role = str((ev.get("semantic") or {}).get("role") or (ev.get("target") or {}).get("role") or "").strip().lower()
    if role in _CONTROL_ROLES:
        return 0
    ancestors = ev.get("ancestors") or []
    for i in range(min(len(ancestors) - 1, _PEER_SEARCH_DEPTH)):
        row = ancestors[i] or {}
        parent = ancestors[i + 1] or {}
        tag = str(row.get("tag") or "").strip().lower()
        if not _names_a_data_row(row, tag) or str(parent.get("tag") or "").strip().lower() in _HEADER_PARENTS:
            continue
        parent_html = str(parent.get("outer_html") or "")
        classes = set(strip_dynamic_classes([str(c) for c in (row.get("classes") or [])]))
        siblings = _direct_child_count(parent_html, tag, classes)
        if len(parent_html) >= _ANCESTOR_HTML_CAP:
            return max(siblings, _MIN_SIBLINGS + 1)
        if siblings >= _MIN_SIBLINGS:
            return siblings
    return 0


def detect_entity_binding(ev: Step) -> EntityBinding | None:
    """Walk the recorded ancestor chain outward; the first ancestor with >=2 structurally
    matching siblings is treated as the row. Returns None when nothing repeats — there is no
    wrong row to guard against."""
    ancestors = ev.get("ancestors") or []
    for i in range(min(len(ancestors) - 1, _MAX_ROW_SEARCH_DEPTH)):
        row = ancestors[i] or {}
        parent = ancestors[i + 1] or {}
        row_tag = str(row.get("tag") or "").strip().lower()
        if not row_tag:
            continue
        row_classes = strip_dynamic_classes([str(c) for c in (row.get("classes") or [])])
        siblings = _direct_child_count(str(parent.get("outer_html") or ""), row_tag, set(row_classes))
        if siblings < _MIN_SIBLINGS:
            continue
        identifier = _pick_identifier(row)
        if not identifier:
            continue
        container_selector = _build_container_selector(parent, row_tag, _css_class_selector(row_classes))
        return EntityBinding(
            container_selector=container_selector,
            identifier=identifier,
            source="literal",
            confirmed=False,
        )
    return None


def collect_input_literal_values(events: list[Step], policy: dict[str, Any]) -> dict[str, str]:
    """{{input_name}} -> the literal text it was recorded with, for upgrading a literal entity
    identifier to a run-input reference when the run already supplies that record's identity."""
    out: dict[str, str] = {}
    for ev in events:
        _, binding = derive_input_binding(ev, policy)
        if not binding or binding in out:
            continue
        raw = (ev.get("action") or {}).get("value")
        if raw is None or str(raw).strip() == "":
            continue
        out[binding] = str(raw)
    return out


def upgrade_entity_binding_identifiers(steps: list[Any], input_literal_values: dict[str, str]) -> None:
    """Prefer a declared run input over a recorded literal for the identifier (Answer 12: "the run
    already knows its inputs"). Mutates in place; no-op where nothing matches."""
    if not input_literal_values:
        return
    for step in steps:
        eb = getattr(step, "entity_binding", None)
        if eb is None or eb.source != "literal" or not eb.identifier:
            continue
        ident_lower = eb.identifier.lower()
        for name, literal in input_literal_values.items():
            literal_lower = literal.lower().strip()
            if not literal_lower:
                continue
            if literal_lower in ident_lower or ident_lower in literal_lower:
                eb.identifier = "{{%s}}" % name
                eb.source = "input"
                break
