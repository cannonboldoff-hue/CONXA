"""Turn the compile's own events into the progress card's rows (BUILD-39).

The compile card shows real stages — nothing is estimated or animated. A stage only changes when
an event the compiler actually emitted says so. The compiler runs the selector work, the
assertions and the second opinion as one call, so the card has four honest rows rather than the
mockup's five: the second opinion is a single model pass that both works out what each step is for
and which typed values change between runs, and splitting it into two rows would invent progress
nobody can observe.

``CompileProgress.observe(event)`` takes the same dicts ``handlers/compile.py`` emits
(``compile_step``, ``pipeline_done``, ``compile_log``, ``quota``, ``compile_done`` ...) and
returns True when something worth showing changed.
"""

from __future__ import annotations

from typing import Any

STAGES: tuple[tuple[str, str], ...] = (
    ("clean", "Cleaned up the recording"),
    ("find", "Learned how to find each element"),
    ("meaning", "Working out what each step is for and which values change"),
    ("save", "Saving the workflow"),
)
_ORDER = [k for k, _ in STAGES]


class CompileProgress:
    def __init__(self) -> None:
        self.status: dict[str, str] = {k: "pending" for k in _ORDER}
        self.detail: dict[str, str] = {k: "" for k in _ORDER}
        self.total = 0          # steps the pipeline produced; what "n of m" counts against
        self.found = 0          # steps compiled so far
        self.step_count = 0     # final, from compile_done
        self.credits_left: int | None = None
        self.failed_stage: str | None = None
        self.error = ""
        self.done = False

    # ── state changes ───────────────────────────────────────────────────────────────────────

    def _start(self, key: str) -> bool:
        changed = False
        for k in _ORDER[: _ORDER.index(key)]:
            if self.status[k] in ("pending", "running"):
                self.status[k] = "done"
                changed = True
        if self.status[key] == "pending":
            self.status[key] = "running"
            changed = True
        return changed

    def _finish(self, key: str, detail: str | None = None) -> bool:
        changed = self.status[key] != "done"
        self.status[key] = "done"
        if detail is not None and detail != self.detail[key]:
            self.detail[key] = detail
            changed = True
        return changed

    def observe(self, ev: dict[str, Any]) -> bool:
        phase = str(ev.get("phase") or "")
        if phase == "quota":
            left = ev.get("remaining", ev.get("compile_credits_remaining"))
            if isinstance(left, (int, float)) and ev.get("status") == "committed":
                self.credits_left = int(left)
                return True
            return False
        if phase in ("pipeline_start",):
            return self._start("clean")
        if phase == "pipeline_done":
            self.total = int(ev.get("event_count") or 0)
            return self._finish("clean", f"{self.total} action{'s' if self.total != 1 else ''} kept")
        if phase == "compiler_start":
            return self._start("find")
        if phase == "compile_log":
            msg = str(ev.get("message") or "")
            if msg.startswith("Compiled step") and ev.get("step_index") is not None:
                n = int(ev["step_index"]) + 1
                if n > self.found:
                    self.found = n
                    self._start("find")
                    self.detail["find"] = f"{min(n, self.total or n)} of {self.total or n}"
                    return True
                return False
            if msg.startswith("Asking for a second opinion"):
                return self._finish("find", f"{self.total} of {self.total}" if self.total else None) | self._start("meaning")
            if msg.startswith("Second opinion finished"):
                return self._finish("meaning")
            return False
        if phase == "compiler_done":
            self.step_count = int(ev.get("step_count") or 0)
            changed = self._finish("find", f"{self.step_count} of {self.step_count}")
            changed |= self._finish("meaning")
            return self._start("save") | changed
        if phase == "compile_done":
            self.step_count = int(ev.get("step_count") or self.step_count)
            self._finish("save")
            self.done = True
            return True
        if phase == "compile_error":
            running = next((k for k in _ORDER if self.status[k] == "running"), None)
            self.failed_stage = running or "find"
            self.status[self.failed_stage] = "failed"
            self.error = str(ev.get("message") or "")
            return True
        return False

    # ── what the card shows ─────────────────────────────────────────────────────────────────

    def rows(self) -> list[dict[str, str]]:
        return [{"key": k, "label": label, "status": self.status[k], "detail": self.detail[k]} for k, label in STAGES]
