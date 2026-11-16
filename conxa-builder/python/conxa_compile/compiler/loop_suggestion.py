"""Deterministic detection of a single-file download->upload pattern that could generalize into
a `for_each` loop over a caller-supplied list of files.

No LLM involved anywhere in this module — the decision to propose is a pure structural pattern
match over facts the compiler already computes (`upload_binding.py`'s single-download binding,
a literal filename appearing verbatim in an earlier `navigate` step's URL). This is deliberately
narrower than the general "was this clicked element part of a list" question: it only ever
proposes generalizing a URL literal, never invents or touches a selector, matching (more
conservatively than) BUILD-26's own rule that a Human Review suggestion may never invent a
selector for something never observed (see docs/TRD.md).

Findings are advisory only, stored under compile_report["for_each_suggestions"] — nothing here
mutates a step. Applying one is `editor/workflow_mutations.py::apply_for_each_loop_suggestion`,
gated behind an explicit Human Edit "Accept" click (`handlers/copilot.py`), same governance model
as BUILD-25's second-opinion findings and BUILD-26's Copilot proposals.

The `why` string built below is later, optionally, replaced by a friendlier sentence from
the compile's review call (`build.py::_rewrite_loop_suggestions_for_review`, BUILD-55) before
it reaches the Human Edit review popup — copy-only, runs after this module returns, and never
changes anything this module decided. This module's own "no LLM" claim above still holds.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import unquote

from conxa_compile.compiler.step_key import step_keys
from conxa_compile.compiler.upload_binding import parse_download_suggested_filename

Step = Any  # SkillStep model instance, as build.py holds it before model_dump()

_SINGLE_DOWNLOAD_PLACEHOLDER = "{{downloaded_file}}"


def url_contains_literal(url: str, literal: str) -> bool:
    """True when `literal` appears in `url` once percent-encoding is undone.

    A browser encodes URL-reserved characters in a path segment — a recorded filename like
    "C++.gitignore" shows up in the actual navigate URL as ".../C%2B%2B.gitignore". A raw
    substring check against the recorded (decoded) filename silently misses every filename
    containing such a character (+, space, non-ASCII, ...) — this bit the very first real
    filename that had one. Decoding the URL, rather than re-encoding the filename, is the
    correct direction: percent-encoding is ambiguous going the other way (a space can become
    "%20" or "+" depending on context/site) but %XX decoding is not. Shared by both the
    detector below and `editor/workflow_mutations.py::apply_for_each_loop_suggestion`, which
    must find and replace the same literal inside the same URL — a second hand-rolled substring
    check there would silently reintroduce this exact bug.
    """
    return literal in unquote(url)


def replace_url_literal(url: str, literal: str, replacement: str) -> str | None:
    """Returns `url` with `literal` replaced by `replacement`, decoding percent-encoding first
    so the replace can find a literal containing a reserved character (see
    `url_contains_literal` above) — or None if `literal` isn't present even after decoding.

    The returned URL is intentionally the DECODED form (e.g. a literal "+" rather than "%2B").
    This loses nothing at replay: `runtime/app/handlers.js`'s navigate handler hands the URL
    straight to Playwright's `page.goto()`, which accepts unencoded reserved characters in a
    path and lets the browser encode them on the wire — the same navigation either way.
    """
    decoded = unquote(url)
    if literal not in decoded:
        return None
    return decoded.replace(literal, replacement)


def _action_name(step: Step) -> str:
    action = getattr(step, "action", None)
    return action if isinstance(action, str) else str((action or {}).get("action") or "")


def _fingerprint_contains(step: Step, literal: str) -> bool:
    """True when `literal` appears verbatim in the recorded click target's own text — the
    same already-materialized IdentityBundle fields the compiler stored at record time, never
    a selector. Checked before absorbing a preceding click into the wrap range (see
    _redundant_click_index below): only ever reads, never invents or infers."""
    fingerprint = getattr(getattr(step, "identity_bundle", None), "fingerprint", None)
    if fingerprint is None:
        return False
    for field in ("inner_text", "aria_label", "label_text", "title"):
        if literal in str(getattr(fingerprint, field, "") or ""):
            return True
    return False


def _redundant_click_index(steps: list[Step], navigate_idx: int, filename: str) -> int | None:
    """The step right before the loop's per-item navigate is redundant when it's a hardcoded
    click on the very file the loop will visit N times — the navigate immediately overrides
    whatever it picked. Only ever true for a click/dblclick on the SAME tab whose own recorded
    text contains the filename verbatim; otherwise leave it alone."""
    j = navigate_idx - 1
    if j < 0 or _action_name(steps[j]) not in {"click", "dblclick"}:
        return None
    # `tab` is a plain dict field (SkillStep.tab), never a model — .get(), not getattr().
    prior_tab = getattr(steps[j], "tab", None) or {}
    nav_tab = getattr(steps[navigate_idx], "tab", None) or {}
    if prior_tab.get("id") != nav_tab.get("id"):
        return None
    if not _fingerprint_contains(steps[j], filename):
        return None
    return j


def detect_download_upload_loop_candidates(steps: list[Step]) -> list[dict[str, Any]]:
    """One candidate per upload step bound to the single-download placeholder whose file was
    selected by navigating to a URL containing its own recorded filename.

    Stateless and skill_id-agnostic by design (compiler layer, run at every compile) — filtering
    out a suggestion a reviewer already dismissed is the caller's job (`handlers/compile.py`,
    which has the skill_id `editor/edit_log.py::read_edits` needs), via `filter_rejected` below —
    same layering `handlers/compile.py` already uses for `filter_runtime_only_inputs`.
    """
    keys = step_keys(steps)
    out: list[dict[str, Any]] = []

    for i, step in enumerate(steps):
        if _action_name(step) not in {"upload", "upload_intent"}:
            continue
        if step.value != _SINGLE_DOWNLOAD_PLACEHOLDER or step.input_binding is not None:
            continue
        upload_key = keys[i]

        # Walk back to the nearest preceding download_observed, then continue back to the
        # nearest preceding navigate whose URL contains that download's own filename verbatim.
        download_idx = None
        for j in range(i - 1, -1, -1):
            if _action_name(steps[j]) == "download_observed":
                download_idx = j
                break
        if download_idx is None:
            continue
        filename = parse_download_suggested_filename(str(steps[download_idx].value or ""))
        if not filename:
            continue

        navigate_idx = None
        for j in range(download_idx - 1, -1, -1):
            if _action_name(steps[j]) == "navigate" and url_contains_literal(str(steps[j].url or ""), filename):
                navigate_idx = j
                break
        if navigate_idx is None:
            continue

        # A hardcoded click on this exact file, right before the navigate, is redundant once
        # the loop navigates straight to each item's URL — absorb it into the wrap range so
        # Accept can drop it (see apply_for_each_loop_suggestion) instead of leaving a
        # single-file-pinned click stranded ahead of the loop.
        redundant_idx = _redundant_click_index(steps, navigate_idx, filename)
        wrap_start_idx = redundant_idx if redundant_idx is not None else navigate_idx

        suggestion: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "kind": "download_upload_loop",
            "wrap_start_key": keys[wrap_start_idx],
            "wrap_end_key": keys[download_idx],
            "upload_step_key": upload_key,
            "template_literal": filename,
            "suggested_input_name": "files",
            "as_name": "file",
            "why": (
                f"This downloads and uploads one fixed file ({filename!r}). "
                "Let the caller name any number of files instead?"
            ),
            "preview": {"before": "1 fixed file", "after": "Any number of caller-named files"},
        }
        if redundant_idx is not None:
            # Its own separate Yes/No question (ReviewQuestionsDialog.tsx asks it as a second,
            # distinct popup right after the loop question is answered Yes) — never folded into
            # `why`/`preview` above, which describe the loop alone. See
            # apply_for_each_loop_suggestion's `remove_redundant_click` param.
            suggestion["redundant_click_key"] = keys[redundant_idx]
            suggestion["redundant_click_why"] = (
                f"There's also a leftover click that just opens {filename!r}'s page — the "
                "loop's own step already does that. Remove this leftover click?"
            )
        out.append(suggestion)

    return out


_REJECTED_FIELD = "for_each_suggestion"


def filter_rejected(suggestions: list[dict[str, Any]], edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop a suggestion whose `upload_step_key` a reviewer already rejected — `edits` is
    `editor/edit_log.py::read_edits(skill_id)`'s full log; a suggestion is a fresh dict every
    compile (fresh `id`), so matching is by the stable `upload_step_key`/`field` pair the
    reject RPC logs (`handlers/copilot.py::cmd_reject_for_each_suggestion`), not by proposal id.
    """
    rejected_keys = {
        str(e.get("step_key") or "")
        for e in edits
        if e.get("decision") == "rejected" and e.get("field") == _REJECTED_FIELD
    }
    if not rejected_keys:
        return suggestions
    return [s for s in suggestions if s.get("upload_step_key") not in rejected_keys]


# ── Repeated entries: the same form filled in several times with different details ─────────────────────────────────
# Code finds the repeat; the person decides (conversation/clarify.py "repeat_rows"); code rewrites
# (editor/workflow_mutations.py::wrap_repeated_entries). No model decides structure here. Only blocks whose typed
# values differ count: an identical block repeated is a different shape (a counted repeat), not a list of entries.

_REPEAT_VALUE_ACTIONS = frozenset({"type", "fill"})
_REPEAT_EXCLUDED = frozenset({"for_each", "for_each_rows", "agent_span", "outcome_fork", "wait_for_one_of"})
_COLUMN_SUFFIX_RE = re.compile(r"_\d+$")
_COLUMN_ID_RE = re.compile(r"[^a-z0-9_]+")
MAX_REPEAT_COLUMNS = 12


def _step_action(step: Step) -> str:
    action = step.get("action") if isinstance(step, dict) else getattr(step, "action", None)
    return action if isinstance(action, str) else str((action or {}).get("action") or "")


def repeat_signature(steps: list[Step]) -> list[tuple[str, str]]:
    """What each step does to which element, without its values: two copies of a form give the same sequence."""
    return [(_step_action(s), k.rsplit("#", 1)[0]) for s, k in zip(steps, step_keys(steps))]


def _column_name(step: Step, taken: set[str]) -> str:
    binding = str((step.get("input_binding") if isinstance(step, dict) else getattr(step, "input_binding", "")) or "")
    base = _COLUMN_SUFFIX_RE.sub("", binding) or "value"
    base = _COLUMN_ID_RE.sub("_", base.lower()).strip("_") or "value"
    if not base[0].isalpha():
        base = f"v_{base}"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def detect_repeated_entries(steps: list[Step], literal_of: Any) -> dict[str, Any] | None:
    """The longest run of 2+ back-to-back copies of one block of steps that types different values in each copy.

    ``literal_of(step)`` returns the value the person typed in that step (from the recording), or None. Returns
    ``{start_key, block_len, count, columns: [{name, offset}], rows: [{name: value}]}`` or None. A typed value that is
    the same in every copy (a fixed department) stays fixed in the loop; only the ones that differ become columns."""
    sig = repeat_signature(steps)
    keys = step_keys(steps)
    n = len(sig)
    best: tuple[int, int, int] | None = None  # (covered, -start, block_len) -> larger is better
    found: dict[str, Any] | None = None
    for block_len in range(2, n // 2 + 1):
        start = 0
        while start + 2 * block_len <= n:
            block = sig[start:start + block_len]
            if sig[start + block_len:start + 2 * block_len] != block or any(a in _REPEAT_EXCLUDED for a, _ in block):
                start += 1
                continue
            count = 2
            while start + (count + 1) * block_len <= n and sig[start + count * block_len:start + (count + 1) * block_len] == block:
                count += 1
            offsets = [o for o, (a, _) in enumerate(block) if a in _REPEAT_VALUE_ACTIONS]
            values = {o: [literal_of(steps[start + c * block_len + o]) for c in range(count)] for o in offsets}
            varying = [o for o in offsets if all(v is not None for v in values[o]) and len(set(values[o])) > 1]
            score = (count * block_len, -start, -block_len)
            if varying and len(varying) <= MAX_REPEAT_COLUMNS and (best is None or score > best):
                taken: set[str] = set()
                columns = [{"name": _column_name(steps[start + o], taken), "offset": o} for o in varying]
                found = {
                    "start_key": keys[start], "block_len": block_len, "count": count, "columns": columns,
                    # Typed the same in every copy: the loop keeps typing exactly that.
                    "fixed": [{"offset": o, "value": str(values[o][0])} for o in offsets
                              if o not in varying and values[o][0] is not None and len(set(values[o])) == 1],
                    "rows": [{c["name"]: str(values[c["offset"]][r]) for c in columns} for r in range(count)],
                }
                best = score
            start += count * block_len
    return found
