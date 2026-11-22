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

from conxa_compile.compiler.content_free_identity import is_value_text
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


_PRICE_OR_NUMBER = re.compile(r"^[\W_]*\d[\d.,\s]*[\W_]*$")


def _row_segment_sets(rows_html: list[str]) -> list[set[str]]:
    return [{seg.lower() for seg in _row_text_segments(h)} for h in rows_html]


def _sibling_rows_html(ev: Step, row: dict[str, Any], parent: dict[str, Any], container_selector: str,
                       row_tag: str, row_classes: list[str], dom_html: str | None) -> tuple[list[str], bool]:
    """The outer html of every row beside this one, and whether the list is known to be complete.
    The full recorded page is the evidence when there is one (the ancestor chain is cut at 2000 characters, so a
    long list shows only its first rows); the ancestor chain is the fallback."""
    if dom_html:
        try:
            matched = BeautifulSoup(dom_html, "html.parser").select(container_selector)
        except Exception:  # an unusual selector is not a reason to lose the binding
            matched = []
        if len(matched) >= _MIN_SIBLINGS:
            return [str(m) for m in matched], True
    parent_html = str(parent.get("outer_html") or "")
    try:
        root = BeautifulSoup(parent_html, "html.parser").find(True)
    except Exception:
        root = None
    kids: list[str] = []
    if root is not None:
        want = set(row_classes)
        for child in root.find_all(row_tag, recursive=False):
            raw = child.get("class") or []
            raw = raw.split() if isinstance(raw, str) else raw
            if want and set(strip_dynamic_classes([str(c) for c in raw])) != want:
                continue
            kids.append(str(child))
    return kids, len(parent_html) < _ANCESTOR_HTML_CAP


def _same_row(others: list[set[str]], mine: set[str], own: frozenset[str] | set[str]) -> int | None:
    """Which of the saved page's rows is the recorded one. Its html may be cut short (``mine <= row``), or the page was
    saved just after the action changed the row (Edit became Save, the email became a box): then it is the row sharing
    the most identifying text with the recorded one (WF-7 7.1: Carol's changed row counted as "another row named
    Carol", so nothing named her row and her Edit went unbound)."""
    exact = next((i for i, sset in enumerate(others) if mine and mine <= sset), None)
    if exact is not None:
        return exact
    ident = {t for t in mine if t not in _NON_IDENTIFYING_TOKENS and t not in own}
    best = max(range(len(others)), key=lambda i: len(ident & others[i]), default=None)
    return best if best is not None and ident & others[best] else None


def _own_texts(ev: Step) -> set[str]:
    """What the clicked control says about itself. It repeats on every row, so it never says which row."""
    target = ev.get("target") if isinstance(ev.get("target"), dict) else {}
    keys = ("inner_text", "aria_label", "label_text", "title", "value", "alt", "placeholder")
    return {str(target.get(k)).strip().lower() for k in keys if str(target.get(k) or "").strip()}


def _pick_identifier(row: dict[str, Any], *, own: frozenset[str] | set[str] = frozenset(),
                     siblings: list[set[str]] | None = None, complete: bool = False) -> str:
    """The text that picks THIS row out. It must not be the control's own label, and when the other rows are known it
    must appear in no other row (two products can cost the same; two rows can share a status word). Words win over bare
    numbers and prices; a code that mixes both (an invoice or order number) is the best of all."""
    candidates = []
    for seg in _row_text_segments(str(row.get("outer_html") or "")):
        if not seg or len(seg) > _MAX_IDENTIFIER_LEN:
            continue
        if seg.lower() in _NON_IDENTIFYING_TOKENS or seg.lower() in own:
            continue
        if is_low_quality_anchor(seg):
            continue
        candidates.append(seg)
    if not candidates:
        return ""
    if siblings is not None and len(siblings) >= _MIN_SIBLINGS:
        # Unique = in no OTHER row. The row itself may be missing from the evidence (a long list's html is cut short),
        # so take it out once if it is there rather than assume it is: otherwise text this row shares with one other
        # row ("$15.99") counted as unique and the row's own name, absent from the cut list, did not.
        others = list(siblings)
        mine = {seg.lower() for seg in _row_text_segments(str(row.get("outer_html") or ""))}
        same = _same_row(others, mine, own)
        if same is not None:
            others.pop(same)
        unique = [c for c in candidates if not any(c.lower() in sset for sset in others)]
        if unique:
            candidates = unique
        elif complete:
            return ""  # every candidate repeats in another row: nothing here can name this row
    def score(c: str) -> tuple[int, int, int]:
        has_letters = bool(re.search(r"[^\W\d_]", c))
        return (1 if has_letters else 0, 0 if _PRICE_OR_NUMBER.match(c) else 1, len(c))
    return max(candidates, key=score)


# --- is the target one item picked from a list? ----------------------------------------------
# Controls act on the page; the other things a person clicks (a link, an option, a cell) name an
# item. A control inside a row (a per-row "Edit") is still a control.
CONTROL_ROLES = frozenset({
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
    if role in CONTROL_ROLES:
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


def _whole_value_in(value: str, text: str) -> bool:
    """``value`` appears in ``text`` as a whole value ("12345" in "Invoice #12345", never "1" in "$136.46")."""
    v = " ".join(value.lower().split())
    return bool(v) and re.search(rf"(?<![\w.,]){re.escape(v)}(?![\w.,])", " ".join(text.lower().split())) is not None


def typed_values(events: list[Step]) -> list[str]:
    """What the person typed, in order: the values a later row can be the result of (a search, a new entry).
    Keys and hidden (password) fields are never a row's identity."""
    out: list[str] = []
    for ev in events:
        action = ev.get("action") or {}
        if str(action.get("action") or "").lower() not in ("type", "fill"):
            continue
        if str((ev.get("semantic") or {}).get("input_type") or "").lower() == "password":
            continue
        value = str(action.get("value") or "").strip()
        if 2 <= len(value) <= _MAX_IDENTIFIER_LEN and "{{" not in value:
            out.append(value)
    return out


def edited_values(events: list[Step]) -> set[str]:
    """The text a field held before the person typed over it (an inline-edited email, a renamed title). That text
    changes when the run saves, so naming the row by it would make the next run miss the row."""
    out: set[str] = set()
    for ev in events:
        if str((ev.get("action") or {}).get("action") or "").lower() not in ("type", "fill"):
            continue
        tag = str((ev.get("target") or {}).get("tag") or "").strip().lower()
        parent_html = str(((ev.get("ancestors") or [{}])[0] or {}).get("outer_html") or "")
        if tag not in ("input", "textarea") or not parent_html:
            continue
        try:
            fields = BeautifulSoup(parent_html, "html.parser").find_all(tag)
        except Exception:
            continue
        # shortcut: every field of that kind in the field's own parent, not the one field; a parent rarely holds two.
        for field in fields:
            old = field.get_text() if tag == "textarea" else field.get("value")
            if isinstance(old, str) and old.strip():
                out.add(old.strip().lower())
    return out


def _searched_row_binding(ev: Step, typed_before: list[str], dom_html: str | None = None) -> EntityBinding | None:
    """A data row that shows a value the person typed earlier is that value's row: the result of a search, or the
    entry just added. It is bound even when it is the only row (a search that finds one record shows one row; on the
    next run it may show none, or a look-alike elsewhere on the page), so the run acts on that row or stops."""
    if not typed_before:
        return None
    ancestors = ev.get("ancestors") or []
    # (row, its list, index of the first ancestor above the list)
    pairs = [(ancestors[i] or {}, ancestors[i + 1] or {}, i + 2) for i in range(min(len(ancestors) - 1, _MAX_ROW_SEARCH_DEPTH))]
    # A clicked option is the target itself (bridge.js::resolveMeaningfulTarget), so its chain starts at the list: the
    # option is the row (BUILD-102 (6): OrangeHRM's picked suggestion was never bound).
    item = _clicked_item(ev)
    if item is not None and ancestors:
        pairs.insert(0, (item, ancestors[0] or {}, 1))
    for row, parent, above in pairs:
        row_tag = str(row.get("tag") or "").strip().lower()
        if row is not item and (not row_tag or not _names_a_data_row(row, row_tag)
                                or str(parent.get("tag") or "").strip().lower() in _HEADER_PARENTS):
            continue
        segments = [row["text"]] if row is item else _row_text_segments(str(row.get("outer_html") or ""))
        for value in reversed(typed_before):  # the latest typed value is the one this row answers
            # (the control's own label is not excluded here: a field's label is often the row's name, and a value the
            # person typed is never a label repeated on every row unless it is one of these words)
            if value.lower() in _NON_IDENTIFYING_TOKENS:
                continue
            if any(_whole_value_in(value, seg) for seg in segments):
                # A suggestion list item shows more than what was typed ("Amelia Brown" for "Amelia"): the row IS the
                # element, and it holds the typed words rather than being them. Inert until the person says so.
                suggestion = _is_suggestion_row(row, parent, segments, value)
                if row is item:
                    if not suggestion:
                        continue  # the option is exactly what was typed: binding it would change what is clicked
                    container = (f'[role="{_outer_role(parent)}"] > [role="{row["role"]}"]'
                                 if _outer_role(parent) in _SUGGESTION_PARENT_ROLES else f'[role="{row["role"]}"]')
                else:
                    row_classes = strip_dynamic_classes([str(c) for c in (row.get("classes") or [])])
                    container = _scope_to_panel(_build_container_selector(parent, row_tag, _css_class_selector(row_classes)),
                                                ancestors[above:], dom_html)
                return EntityBinding(
                    container_selector=container,
                    identifier=value,
                    source="literal",
                    confirmed=False,
                    recorded_matches=_rows_showing(container, value, dom_html),
                    **({"match": "contains", "self_target": True} if suggestion else {}),
                )
    return None


def _rows_showing(container: str, value: str, dom_html: str | None) -> int:
    """How many of the saved page's rows show ``value`` in full (BUILD-109 (1): over 1 = twin rows). 0 = no saved page."""
    if not dom_html:
        return 0
    try:
        rows = BeautifulSoup(dom_html, "html.parser").select(container)
    except Exception:  # an unusual selector is not a reason to lose the binding
        return 0
    return sum(1 for r in rows if any(_whole_value_in(value, seg) for seg in _row_text_segments(str(r))))


_SUGGESTION_ROLES = frozenset({"option", "menuitem"})
_SUGGESTION_PARENT_ROLES = frozenset({"listbox", "menu"})


def _clicked_item(ev: Step) -> dict[str, Any] | None:
    """The clicked target as a row, when it is a list item the page offers as a choice (role option / menuitem)."""
    target = ev.get("target") if isinstance(ev.get("target"), dict) else {}
    role = str(target.get("role") or "").strip().lower()
    text = " ".join(str(target.get("inner_text") or "").split())
    if role not in _SUGGESTION_ROLES or not text:
        return None
    return {"tag": target.get("tag"), "classes": target.get("classes") or [], "role": role, "text": text}


def _outer_role(el: dict[str, Any]) -> str:
    if el.get("role"):
        return str(el["role"]).lower()
    m = _ROLE_ATTR_RE.match(str(el.get("outer_html") or ""))
    return m.group(1).lower() if m else ""


_PANEL_SEARCH_DEPTH = 8


def _stable_selector(el: dict[str, Any]) -> str:
    el_id = str(el.get("id") or "").strip()
    if el_id and re.fullmatch(r"[A-Za-z_-][\w-]*", el_id) and not is_dynamic_id(f"#{el_id}"):
        return f"#{el_id}"
    tag = str(el.get("tag") or "").strip().lower()
    classes = _css_class_selector(strip_dynamic_classes([str(c) for c in (el.get("classes") or [])]))
    return f"{tag}{classes}" if tag and classes else ""


def _scope_to_panel(container: str, above: list[dict[str, Any]], dom_html: str | None) -> str:
    """The same rows can show in two panels ("Results" and "Recently edited", both ``tbody > tr``): the record then
    shows twice and every run refuses as "more than one row" (BUILD-108 (3)). Put the nearest ancestor above the list
    whose id or classes leave only one list's rows in front of it. Read from the saved page only: no page, no change,
    and a panel nothing names keeps the plain selector (the runtime still refuses rather than guess)."""
    if not dom_html or container.startswith("#"):  # a list with its own id is one list already
        return container
    try:
        soup = BeautifulSoup(dom_html, "html.parser")
        if len({id(r.parent) for r in soup.select(container)}) < 2:
            return container
    except Exception:  # an unusual selector is not a reason to lose the binding
        return container
    for el in above[:_PANEL_SEARCH_DEPTH]:
        prefix = _stable_selector(el or {})
        if not prefix:
            continue
        scoped = f"{prefix} {container}"
        try:
            hits = soup.select(scoped)
        except Exception:
            continue
        if hits and len({id(r.parent) for r in hits}) == 1:
            return scoped
    return container


def _is_suggestion_row(row: dict[str, Any], parent: dict[str, Any], segments: list[str], value: str) -> bool:
    """A list item the page offers as a choice (role option / menuitem, or inside a listbox / menu) whose words go beyond
    the typed value: picking it is "the suggestion that shows what was typed", not "this exact text"."""
    if _outer_role(row) not in _SUGGESTION_ROLES and _outer_role(parent) not in _SUGGESTION_PARENT_ROLES:
        return False
    want = " ".join(value.lower().split())
    return len(want) >= 2 and any(" ".join(seg.lower().split()) != want and want in " ".join(seg.lower().split())
                                  for seg in segments)


def detect_entity_binding(
    ev: Step, *, dom_html: str | None = None, destructive: bool = False, address_unique: bool = False,
    typed_before: list[str] | None = None, edited: set[str] | frozenset[str] = frozenset(),
) -> EntityBinding | None:
    """The row a step acts on (``_row_binding``), or, for a captured value no words can find, its place in the list
    (``_position_binding``, inert until the person says yes)."""
    # shortcut: a capture that got a words binding (two rows with the same price) is not offered the place; add it if seen.
    found = _row_binding(ev, dom_html=dom_html, destructive=destructive, address_unique=address_unique,
                         typed_before=typed_before, edited=edited)
    return found if found is not None else _position_binding(ev, dom_html)


# clarify.py words the place up to "tenth"; nobody means "always the 14th result".
_MAX_POSITION = 10


def _position_binding(ev: Step, dom_html: str | None) -> EntityBinding | None:
    """A captured value whose words change every run ("Rs. 400", the first result's price, BUILD-101 (7)) cannot name
    its row, and the row's other words change with the search too. What stays is its place: the Nth row of a list that
    is the only such list on the saved page, the recorded row found there exactly once. The binding is inert until the
    person answers "Always the first result?" with yes (conversation/clarify.py, runtime/app/resolution.js::
    isInertBinding). No saved page, no binding."""
    extras = ev.get("extras") if isinstance(ev.get("extras"), dict) else {}
    spec = extras.get("extract") if isinstance(extras.get("extract"), dict) else {}
    target = ev.get("target") if isinstance(ev.get("target"), dict) else {}
    if (str((ev.get("action") or {}).get("action") or "").lower() != "extract" or str(spec.get("source") or "text") != "text"
            or not dom_html or not is_value_text(target.get("inner_text"))):
        return None
    try:
        soup = BeautifulSoup(dom_html, "html.parser")
    except Exception:
        return None
    ancestors = ev.get("ancestors") or []
    for i in range(min(len(ancestors) - 1, _MAX_ROW_SEARCH_DEPTH)):
        row, parent = ancestors[i] or {}, ancestors[i + 1] or {}
        row_tag = str(row.get("tag") or "").strip().lower()
        if not row_tag or not _names_a_data_row(row, row_tag) \
                or str(parent.get("tag") or "").strip().lower() in _HEADER_PARENTS:
            continue
        row_classes = strip_dynamic_classes([str(c) for c in (row.get("classes") or [])])
        container = _scope_to_panel(_build_container_selector(parent, row_tag, _css_class_selector(row_classes)),
                                    ancestors[i + 2:], dom_html)
        try:
            rows = soup.select(container)
        except Exception:  # an unusual selector is not a reason to fail the compile
            continue
        mine = {seg.lower() for seg in _row_text_segments(str(row.get("outer_html") or ""))}
        at = [n for n, r in enumerate(rows) if mine and mine <= {seg.lower() for seg in _row_text_segments(str(r))}]
        if len(rows) >= _MIN_SIBLINGS and len({id(r.parent) for r in rows}) == 1 and len(at) == 1 and at[0] < _MAX_POSITION:
            return EntityBinding(container_selector=container, position=at[0] + 1, confirmed=False)
    return None


def _row_binding(
    ev: Step, *, dom_html: str | None = None, destructive: bool = False, address_unique: bool = False,
    typed_before: list[str] | None = None, edited: set[str] | frozenset[str] = frozenset(),
) -> EntityBinding | None:
    """Walk the recorded ancestor chain outward; the first ancestor with >=2 structurally
    matching siblings is treated as the row. Returns None when nothing repeats — there is no
    wrong row to guard against.

    A row binding exists to tell rows apart, so it is built only where that is needed and possible:
    - a step that cannot do harm and whose own address already picks exactly one element on the recorded page
      (``address_unique``, e.g. a ``data-test`` id) needs no row, and a binding there can only add a way to fail;
    - the identifier must appear in one row only (see ``_pick_identifier``). When no text can name the row, a step
      that cannot do harm gets no binding (the runtime still refuses to guess among look-alike elements), while a
      step that can do harm keeps its best guess, unconfirmed, so a person is asked to check which row it means.

    A row showing a value typed earlier in the recording (``typed_before``: a search, a new entry) is bound by that value
    before any of this (``_searched_row_binding``). Text the workflow itself types over (``edited``) never names a row."""
    searched = _searched_row_binding(ev, list(typed_before or []), dom_html)
    if searched is not None:
        return searched
    if address_unique and not destructive and dom_html:
        return None
    own = _own_texts(ev)
    ancestors = ev.get("ancestors") or []
    for i in range(min(len(ancestors) - 1, _MAX_ROW_SEARCH_DEPTH)):
        row = ancestors[i] or {}
        parent = ancestors[i + 1] or {}
        row_tag = str(row.get("tag") or "").strip().lower()
        # Only a data row can name a record. Two layout panels side by side are not a list (WF-3 D: ParaBank's menu
        # links were bound to `#bodyPanel > div` "Update Contact Info", the other panel's text).
        if not row_tag or not _names_a_data_row(row, row_tag) \
                or str(parent.get("tag") or "").strip().lower() in _HEADER_PARENTS:
            continue
        row_classes = strip_dynamic_classes([str(c) for c in (row.get("classes") or [])])
        siblings = _direct_child_count(str(parent.get("outer_html") or ""), row_tag, set(row_classes))
        if siblings < _MIN_SIBLINGS:
            continue
        container_selector = _scope_to_panel(_build_container_selector(parent, row_tag, _css_class_selector(row_classes)),
                                             ancestors[i + 2:], dom_html)
        rows_html, complete = _sibling_rows_html(ev, row, parent, container_selector, row_tag, row_classes, dom_html)
        # The control's own words appear in this row only (an account number link): its own address already picks the
        # row, and a binding on other row text (a balance that changes after a transfer) only adds a way to fail.
        # Holds on a cut list too: a step that cannot do harm without a binding still never acts on a look-alike guess.
        if not destructive and own and len(rows_html) >= _MIN_SIBLINGS:
            others = _row_segment_sets(rows_html)
            mine = {seg.lower() for seg in _row_text_segments(str(row.get("outer_html") or ""))}
            same = _same_row(others, mine, own)
            if same is not None:
                others.pop(same)
            if any(t in mine and not any(t in sset for sset in others) for t in own):
                return None
        not_identity = own | set(edited)
        identifier = _pick_identifier(row, own=not_identity,
                                      siblings=_row_segment_sets(rows_html) if rows_html else None, complete=complete)
        if not identifier and destructive:
            identifier = _pick_identifier(row, own=not_identity)  # best guess, left unconfirmed for a person to check
        if not identifier:
            continue
        return EntityBinding(
            container_selector=container_selector,
            identifier=identifier,
            source="literal",
            confirmed=False,
        )
    return None


# Signals that find an element only by where it sits: the third Edit button is someone else's after the list re-sorts.
POSITIONAL_ENGINES = frozenset({"xpath", "css-structural"})


def row_action_in_list(ev: Step, *, dom_html: str | None = None) -> bool:
    """True for a control repeated on the rows of a list (every row's Edit or Delete): only a row binding can say which
    row it acts on. A control whose own words name its row (an account number link, the one Save) is not one."""
    own = _own_texts(ev)
    if not own:
        return False
    ancestors = ev.get("ancestors") or []
    for i in range(min(len(ancestors) - 1, _MAX_ROW_SEARCH_DEPTH)):
        row = ancestors[i] or {}
        parent = ancestors[i + 1] or {}
        row_tag = str(row.get("tag") or "").strip().lower()
        if not row_tag or not _names_a_data_row(row, row_tag)                 or str(parent.get("tag") or "").strip().lower() in _HEADER_PARENTS:
            continue
        row_classes = strip_dynamic_classes([str(c) for c in (row.get("classes") or [])])
        container_selector = _build_container_selector(parent, row_tag, _css_class_selector(row_classes))
        rows_html, _ = _sibling_rows_html(ev, row, parent, container_selector, row_tag, row_classes, dom_html)
        if len(rows_html) < _MIN_SIBLINGS:
            continue
        others = _row_segment_sets(rows_html)
        mine = {seg.lower() for seg in _row_text_segments(str(row.get("outer_html") or ""))}
        same = _same_row(others, mine, own)
        if same is not None:
            others.pop(same)
        if any(t in mine and not any(t in sset for sset in others) for t in own):
            return False
        return any(t in sset for t in own for sset in others)
    return False


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
        ident_lower = " ".join(eb.identifier.lower().split())
        for name, literal in input_literal_values.items():
            literal_lower = " ".join(literal.lower().split())
            if not literal_lower:
                continue
            # The whole value only ("12345" in "Invoice #12345"): a select's recorded value "1" is inside "$136.46", and
            # a row named by part of what was typed is not the row the typed value names (WF-3 D made every balance a
            # phantom {{account type}} input).
            if _whole_value_in(literal_lower, ident_lower):
                # Only the typed part becomes the input ("Invoice #{{invoice_no}}"): the runtime matches a row's cell as
                # a whole value, so "12346" alone would never equal the cell "Invoice #12346".
                eb.identifier = template_identifier(" ".join(eb.identifier.split()), literal, name)
                eb.source = "input"
                break


def template_identifier(identifier: str, literal: str, name: str) -> str:
    """``identifier`` with the whole-value occurrence of ``literal`` replaced by ``{{name}}``, the rest kept as recorded."""
    v = re.escape(" ".join(literal.split()))
    return re.sub(rf"(?<![\w.,]){v}(?![\w.,])", lambda _m: "{{%s}}" % name, identifier, count=1, flags=re.IGNORECASE)
