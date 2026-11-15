"""Canonical `{{variable}}` placeholder grammar — single source of truth shared by the
patch gate, workflow mutations, DTO scanners, and the saved-skill exporter.

`runtime/run.js`'s `interpolate()` mirrors this exact id grammar (letter-led,
alnum + underscore) — keep the two in sync if this ever changes.
"""

from __future__ import annotations

import re

# What an INPUT may be called. A reference is wider: `{{row.to}}` names a column of the current row of a rows loop
# (`<as>.<column>`, one dot). Such a name is never an input (see upload_binding.for_each_loop_variable_names).
# Shared fixture with the runtime's interpolate.js: runtime/test/fixtures/placeholder_cases.json.
PLACEHOLDER_ID_PATTERN = r"[a-zA-Z][a-zA-Z0-9_]*"
PLACEHOLDER_REF_PATTERN = PLACEHOLDER_ID_PATTERN + r"(?:\." + PLACEHOLDER_ID_PATTERN + r")?"

# Finds every {{id}} / {{row.column}} occurrence in a string (used for scanning/extraction).
PLACEHOLDER_RE = re.compile(r"\{\{\s*(" + PLACEHOLDER_REF_PATTERN + r")\s*\}\}")

# Matches only when the ENTIRE string is a single placeholder (used to detect
# "this field is exactly one bound variable" vs. mixed literal+placeholder text).
FULL_PLACEHOLDER_RE = re.compile(r"^\{\{\s*(" + PLACEHOLDER_REF_PATTERN + r")\s*\}\}$")

# Finds any {{...}} span regardless of what's inside the braces — used to catch
# malformed placeholder attempts (hyphens, leading digits, empty) that PLACEHOLDER_RE
# would silently skip over.
LOOSE_BRACE_RE = re.compile(r"\{\{[^{}]*\}\}")


def is_valid_placeholder_id(value: str) -> bool:
    return bool(re.fullmatch(PLACEHOLDER_ID_PATTERN, value or ""))
