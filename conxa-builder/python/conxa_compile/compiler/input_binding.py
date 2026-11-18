"""Input binding derivation: turn typed values into named template parameters.

Priority order (deterministic, no LLM needed):
1. label_text (e.g., "First Name" → {{first_name}})
2. placeholder (e.g., "Search for labels" → {{search_for_labels}})
3. aria_label
4. Value pattern detection (email regex → {{email}}, phone digits → {{phone}})
5. semantic.input_type (last resort)

Keyboard events (Enter, ArrowDown, etc.) are preserved as literal key names.
"""

from __future__ import annotations

import json
import re
from typing import Any

_EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_PHONE_REGEX = re.compile(r"^\+?[\d\s\-\(\)]{7,}$")
_DIGITS_ONLY_REGEX = re.compile(r"^\d{4,}$")
_URL_REGEX = re.compile(r"^https?://", re.IGNORECASE)
# ISO 8601 date/datetime — checked before phone, whose [\d\s\-\(\)]{7,} pattern otherwise also
# matches "2026-09-14" (8+ digits with hyphens) and misclassifies every date_pick collapse's
# ISO literal (compiler/date_picker.py) as a phone number.
_ISO_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")
# Typed numeric dates ("25-09-2026", "25/09/2026", "25.09.2026") — same reason: "25-09-2026" also
# satisfies the phone pattern below. Only a real calendar date (detect_date_format may still say
# the day/month order is ambiguous, which is fine here: it IS a date either way).
_TYPED_DATE_REGEX = re.compile(r"^\d{1,2}([/.\-])\d{1,2}\1\d{4}$")


def _snake_case(text: str) -> str:
    """Convert arbitrary text to a snake_case binding name.

    Examples:
        "First Name" → "first_name"
        "Email Address" → "email_address"
        "Search for labels" → "search_for_labels"
        "Phone #" → "phone"
    """
    if not text:
        return ""
    s = str(text).strip().lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")
    return s


def _classify_value_pattern(value: str) -> str | None:
    """Classify a string value by content pattern.

    Returns 'email', 'phone', 'url', 'date', 'number', or None.
    """
    if not value:
        return None
    v = value.strip()
    if _EMAIL_REGEX.match(v):
        return "email"
    if _URL_REGEX.match(v):
        return "url"
    if _ISO_DATE_REGEX.match(v) or _TYPED_DATE_REGEX.match(v):
        return "date"
    if _PHONE_REGEX.match(v) and any(c.isdigit() for c in v):
        digits = re.sub(r"\D", "", v)
        if len(digits) >= 7:
            return "phone"
    if _DIGITS_ONLY_REGEX.match(v):
        return "number"
    return None


def parse_keyboard_event_value(value: Any) -> str | None:
    """Extract the key name from a keyboard event value.

    Recorder serializes keyboard events as JSON like:
        '{"key":"Enter","code":"Enter","modifiers":{...}}'

    Returns "Enter", "ArrowDown", "Tab", "Shift+Tab", "Control+S", etc. or None if not a keyboard event.
    Modifiers are kept, in the runtime's own order and spelling (runtime locators.js::parseKeyboardShortcut):
    dropping them replayed Ctrl+A as a typed "A" and Shift+Tab as Tab (WF-8).
    """
    if value is None:
        return None
    parsed: Any = value
    if isinstance(value, str):
        s = value.strip()
        if not s.startswith("{"):
            return s
        try:
            parsed = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(parsed, dict) or not parsed.get("key"):
        return None
    key = str(parsed["key"])
    mods = parsed.get("modifiers") if isinstance(parsed.get("modifiers"), dict) else {}
    held = [name for flag, name in (("ctrl", "Control"), ("meta", "Meta"), ("shift", "Shift"), ("alt", "Alt")) if mods.get(flag)]
    if not held:
        return key
    return "+".join([*held, key.upper() if len(key) == 1 else key])


def derive_input_binding(
    ev: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[Any, str | None]:
    """Derive (value, input_binding_name) from event with priority signals.

    For keyboard events: returns (key_name, None) — no template binding.
    For type events: returns ("{{binding}}", binding) using priority signals.
    """
    action = ev.get("action") or {}
    raw_value = action.get("value")
    target = ev.get("target") or {}
    semantic = ev.get("semantic") or {}
    action_type = str(action.get("action") or "").lower()

    # Keyboard events: preserve the actual key name
    if action_type == "keyboard_shortcut":
        key_name = parse_keyboard_event_value(raw_value)
        if key_name:
            return key_name, None
        return raw_value, None

    # Uploads always bind to file_path, never a label-derived name (the label/aria text on the
    # picker element varies per site -- "File Uploader", "Attach document", "Upload CSV" -- which
    # would give every upload skill a differently named input for the same concept).
    if action_type in {"upload", "upload_intent"}:
        return "{{file_path}}", "file_path"

    # A drag's value is its payload (which element was picked up and where it was dropped), not something a person
    # typed or chose. Turning it into an input named after the drop target ("{{products}}") overwrote the payload,
    # so the compiled step lost its source and replayed as a drag that moved nothing.
    if action_type in {"drag_drop", "drag"}:
        return raw_value, None

    if raw_value is None:
        return None, None

    # Check policy credential bindings first (highest priority for known fields)
    input_type = str(semantic.get("input_type") or "").lower()
    sig = policy.get("signals") if isinstance(policy.get("signals"), dict) else {}
    cred = sig.get("credential_bindings") if isinstance(sig.get("credential_bindings"), dict) else {}

    def _credential(name: str) -> tuple[str, str] | None:
        # The policy template names the input ("email" -> "{{user_email}}"). The binding is that same name: a
        # value and binding that disagree ({{user_email}} bound as "email") made one input look like two, so it
        # was never offered as a "changes each time" value and its test box came up without the recorded value.
        for ck, template in cred.items():
            if _snake_case(str(ck)) == name:
                m = re.fullmatch(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", str(template))
                return (str(template), m.group(1)) if m else None
        return None

    # Priorities 1-3: label_text, then placeholder, then aria_label → snake_case binding
    # A caption only found by looking at the text near the box (the recorder says so with label_nearby) can be a
    # neighbouring button's ("Add" beside a search box); the box's own placeholder or aria-label then comes first.
    own_first = bool(target.get("label_nearby")) and (target.get("placeholder") or target.get("aria_label"))
    candidates = (target.get("placeholder"), target.get("aria_label"), target.get("label_text")) if own_first else (
        target.get("label_text"), target.get("placeholder"), target.get("aria_label"))
    for raw in candidates:
        binding = _snake_case(str(raw or "").strip())
        if binding:
            return _credential(binding) or (f"{{{{{binding}}}}}", binding)

    # Priority 4: value pattern detection — skipped for a <select>. Pattern classification is a
    # free-text heuristic ("looks like a phone number", "looks like a number"); a select's value
    # is one of a fixed set of options, not free text, and a chosen option that happens to look
    # like a pattern (a year "2007" reads as _DIGITS_ONLY_REGEX's {{number}}) would bind an
    # unrelated dropdown to whatever OTHER field the caller supplies for that generic name — a
    # 4-digit birth-year <select> silently trying to select the mobile-number input's value.
    is_select_target = action_type == "select" or str(target.get("tag") or "").lower() == "select"
    pattern = None if is_select_target else _classify_value_pattern(str(raw_value or ""))
    if pattern:
        return _credential(pattern) or (f"{{{{{pattern}}}}}", pattern)

    # Priority 5: input_type (last resort — usually just "text")
    if input_type and input_type != "text":
        # Skip generic "text" — it produces useless {{text}} for every field
        binding = input_type.replace("-", "_")
        return _credential(binding) or (f"{{{{{binding}}}}}", binding)

    # Last fallback: use target name or id if available
    name_attr = str(target.get("name") or "").strip()
    if name_attr:
        binding = _snake_case(name_attr)
        if binding:
            return f"{{{{{binding}}}}}", binding

    id_attr = str(target.get("id") or "").strip()
    if id_attr and not id_attr.startswith("downshift-"):
        binding = _snake_case(id_attr)
        if binding:
            return f"{{{{{binding}}}}}", binding

    # Truly nothing identifying — fall back to raw value
    return raw_value, None
