"""Pydantic models for raw recorder events (multi-signal, high fidelity)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ActionKind = Literal[
    # pointer interactions
    "click", "dblclick", "right_click", "hover",
    # text / form
    "type", "fill", "set_checkbox", "set_radio", "select", "select_option", "date_pick",
    # drag / keyboard
    "drag_drop", "keyboard_shortcut",
    # scroll / navigation
    "scroll", "navigate",
    # browser history navigation (Back/Forward buttons, Alt+Left/Right) — captured via the
    # recorder's per-page CDP navigation-history tracking; replayed as goBack/goForward,
    # never as a guessed URL navigation
    "browser_back", "browser_forward",
    # user drove the browser chrome directly (retyped the address bar, picked a bookmark)
    # — no in-page DOM event and no CDP frameRequestedNavigation, so nothing else in the
    # recording would ever reproduce it. Compiles to an ordinary `navigate` step.
    "manual_navigate",
    # browser context
    "tab_open", "tab_switch", "popup", "frame_enter", "frame_exit",
    # file I/O affordances
    "upload_intent", "upload", "download_observed",
    "dialog_appeared", "dialog_accept", "dialog_dismiss",
    "file_chooser_opened",
    "clipboard_copy", "clipboard_paste",
    # control
    "wait", "assert", "screenshot",
    # conditional / branch (EXEC-1): optional interstitials — probe + nested step body, never
    # escalate to recovery. See CLAUDE.md Key Invariants.
    "if_present", "try_dismiss", "wait_for_one_of", "outcome_fork",
    # legacy
    "focus", "check",
]


class ActionMeta(BaseModel):
    action: ActionKind
    timestamp: str
    value: str | None = None


class TargetDom(BaseModel):
    tag: str
    id: str | None = None
    classes: list[str] = Field(default_factory=list)
    inner_text: str = ""
    role: str | None = None
    aria_label: str | None = None
    name: str | None = None
    placeholder: str | None = None
    label_text: str | None = None
    # An <img>'s accessible name comes from alt (title is the generic fallback for any
    # element). Without these, a nameless-looking image had no real name to compile from
    # and fell through to label_text — the nearest surrounding text, which is not a name.
    alt: str | None = None
    title: str | None = None
    # Recorder-captured stable role/data-* attributes (bridge.js::collectIdentityAttrs) —
    # the compiler's named-attr identity signal reads this. Must stay declared here or
    # Pydantic's default extra="ignore" silently drops it on RecordedEvent.model_validate().
    attributes: dict[str, str] = Field(default_factory=dict)


class Selectors(BaseModel):
    css: str = ""
    xpath: str = ""
    text_based: str = ""
    aria: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_null_selectors(cls, values: Any) -> Any:
        """Coerce bridge-sent null selector strings to "" so no event is silently
        dropped when an element has no ARIA or text-based selector."""
        if isinstance(values, dict):
            for field in ("css", "xpath", "text_based", "aria"):
                if values.get(field) is None:
                    values[field] = ""
        return values


class DomContext(BaseModel):
    parent: str
    siblings: list[str] = Field(default_factory=list)
    index_in_parent: int = 0
    form_context: str | None = None


class SemanticFeatures(BaseModel):
    normalized_text: str
    role: str
    input_type: str | None = None
    intent_hint: str


class AnchorRelation(BaseModel):
    element: str
    relation: Literal["below", "above", "inside", "near"]


_REQUIRED_FRAME_KEYS = {"before_far", "before_near", "at", "after_near", "after_far"}


class VisualFeatures(BaseModel):
    full_screenshot: str | None = None
    element_snapshot: str | None = None
    bbox: dict[str, int]
    viewport: str
    scroll_position: str
    # Milliseconds since video recording started. Required for all non-auth events.
    timestamp_ms: int
    # Extracted video frames at T-500ms, T-250ms, T+0ms, T+250ms, T+500ms (relative paths).
    # Empty while recording; populated by the shutdown frame extractor.
    # full_screenshot is set to frames["before_near"] (T-250ms) as the default representative.
    frames: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_frames(self) -> "VisualFeatures":
        if not self.frames:
            return self
        missing = _REQUIRED_FRAME_KEYS - set(self.frames.keys())
        if missing:
            raise ValueError(
                f"VisualFeatures.frames missing required keys: {sorted(missing)} "
                f"(have: {sorted(self.frames.keys())})"
            )
        extra = set(self.frames.keys()) - _REQUIRED_FRAME_KEYS
        if extra:
            raise ValueError(
                f"VisualFeatures.frames has unexpected keys: {sorted(extra)} "
                f"(allowed: {sorted(_REQUIRED_FRAME_KEYS)})"
            )
        for key, path in self.frames.items():
            if not str(path).strip():
                raise ValueError(f"VisualFeatures.frames[{key!r}] is empty")
        return self


class PageContext(BaseModel):
    url: str
    title: str


class StateChange(BaseModel):
    before: str
    after: str
    # Elements added/removed in the interactive-element signature since the action fired
    # ({"added": [...], "removed": [...]}). Emitted by bridge.js's _computeDomDiff on every
    # action; optional so recordings from before this field existed still validate.
    dom_diff: dict[str, Any] | None = None


class DateContext(BaseModel):
    """Custom calendar-widget interaction classification, emitted for `action == "click"` (day
    cell / prev-next nav button / time option) and `action == "select"` (a year/month <select>
    inside the same grid — react-datepicker's showMonthDropdown/showYearDropdown mode and MUI's/
    Ant Design's year-picker views use native selects instead of click-through nav) when
    bridge.js::buildDateContext resolves it to something inside a detected calendar grid. Absent
    (None) for every ordinary click/select, including every native `<input type=date>` (that path
    stays on the existing date_pick "change" listener — never routes through this model at all).
    Feeds compiler/date_picker.py's collapse of the resulting event run into one parameterized
    date_pick step — see CLAUDE.md's date-picker plan."""

    role: Literal["day", "nav", "time", "year_select", "month_select"]
    grid: str = ""  # selector for the widget wrapper (header + nav + day grid)
    # role == "day"
    iso_date: str | None = None
    # Which attribute buildDateContext parsed iso_date from ("data-date", "datetime", "data-day",
    # "data-value", or "" for aria-label/title). Lets the runtime rebuild a query for a DIFFERENT
    # target date ([data-date="<new-iso>"]) instead of reusing this recording's cell selector,
    # which is only ever valid for the literal day it was recorded on.
    cell_attr: str = ""
    header: str = ""
    header_text: str = ""
    prev: str = ""
    next: str = ""
    cell: str = ""
    field: str = ""                       # the input/combobox this grid belongs to, if found
    field_display_value: str | None = None  # field's post-pick display text — reveals site format
    # role == "nav"
    nav: Literal["prev", "next"] | None = None
    # role == "time"
    time: str | None = None
    # role == "year_select" / "month_select": selector for the <select> itself (distinct from
    # `field` above — the widget's own anchored text field, when one exists, is a different
    # element) and the newly-committed option's own label text (e.g. "2006", "December") — never
    # the select's `label_text`, which is the picker's own live "current month" header readout,
    # not a real label (see bridge.js::captureAssociatedLabel's month-year exclusion guard).
    select: str = ""
    value: str | None = None


class ChoiceOption(BaseModel):
    """One member of a recorded multiple-choice group (see ChoiceContext)."""

    value: str = ""
    label: str = ""
    selector: str = ""    # per-option selector; "" for a native <select>'s <option> (no DOM node
                           # worth targeting directly — the runtime acts on the <select> itself)
    checked: bool = False


class ChoiceContext(BaseModel):
    """Multiple-choice control detection (radio/checkbox groups, native <select>, ARIA
    radiogroup/listbox widgets), emitted by bridge.js::buildChoiceContext whenever the recorded
    action lands on one of these AND no DateContext claimed it first (a year/month <select> inside
    a calendar grid is date-picker navigation, not an independent MCQ). Absent (None) for every
    ordinary control, including a standalone checkbox with no group ("I agree") — see
    _CHOICE_GROUP_MIN in bridge.js.

    Captures every option, not just the one clicked: without this, the compiler can only name an
    input after the recorded ANSWER ("male") instead of the QUESTION ("gender"), and the runtime
    has nothing to validate a caller's value against — it just replays whatever was recorded no
    matter what value is passed. Feeds compiler/choice.py's derive_choice(), which produces the
    handler_hints.choice payload and the {{group}}-named input binding — see CLAUDE.md's
    multiple-choice plan (mirrors the date-picker feature's structure)."""

    kind: Literal["radio", "checkbox", "select", "aria_radio", "aria_listbox"]
    multi: bool = False          # true for checkbox groups and <select multiple>
    group_key: str = ""          # name attr / radiogroup id — identifies the group, not an option
    group_label: str = ""        # fieldset <legend> / aria-label(ledby) / the <select>'s own label
    group_selector: str = ""     # container selector (<fieldset>, [role=radiogroup]); "" for <select>
    # Control that reopens a POPUP listbox at replay ([role=combobox][aria-controls=<listbox>],
    # else the nearest stable-id ancestor). A custom dropdown renders its options only while the
    # menu is open, and the click that opens it lands on a role-less container the recorder
    # discards as noise — without this the runtime has nothing to click and the option can only
    # ever miss. "" for an always-visible group (native <select>, radio fieldset).
    opener_selector: str = ""
    options: list[ChoiceOption] = Field(default_factory=list)


class DialogButton(BaseModel):
    """One visible button of a decision dialog, in the same descriptor shape the runtime's
    overlayProbe uses (tag/role/name/text/testid/id) so a later fork can address it."""

    tag: str = ""
    role: str = ""
    name: str = ""
    text: str = ""
    testid: str = ""
    id: str = ""
    chosen: bool = False     # true for the button the person actually clicked while recording


class DialogContext(BaseModel):
    """Full picture of a dialog the recorded action sat inside (or opened): its title, message and
    EVERY button, not just the one clicked. Without it the compiler can only see the winning
    click, so a decision ("Replace / Keep both / Cancel") is indistinguishable from a dismissable
    popup and the recorded choice would replay silently. Absent (None) on every older recording.
    ``message`` is compile-time evidence only and must never be shipped in a skill pack."""

    kind: Literal["dom", "native"] = "dom"
    container_signal: str = ""
    role: str = ""
    title: str = ""
    message: str = Field(default="", max_length=300)
    has_fields: bool = False     # editable inputs inside -> a form, not a decision
    buttons: list[DialogButton] = Field(default_factory=list, max_length=8)


class PostCondition(BaseModel):
    """Post-condition distillation (recording-next-steps.md Priority 1): a small structured
    classification of the before/after delta already captured for this event, computed by plain
    DOM checks in bridge.js::buildPostCondition. Lets the compiler emit a *specific* assertion
    (dialog opened / value committed / navigated) instead of a generic state-change check. All
    fields optional — absent on recordings made before this existed."""

    classified_effect: Literal[
        "navigation", "dialog_opened", "dialog_closed", "expansion", "value_set",
        "content_change", "none",
    ] | None = None
    # Committed field value read back from the DOM at finalize time; "{{REDACTED}}" for
    # password/sensitive fields (same redaction rule bridge.js applies elsewhere).
    value_readback: str | None = None
    url_delta: dict[str, str] | None = None
    # Selector for the dialog/interstitial container, when classified_effect == "dialog_opened".
    dialog_signal: str | None = None


class Timing(BaseModel):
    wait_for: str = "load"
    timeout: int = 5000


class FrameContext(BaseModel):
    """Iframe chain for actions captured inside child documents."""

    chain: list[dict[str, Any]] = Field(default_factory=list)


class TabContext(BaseModel):
    """Which browser tab/page recorded this event. Defaulted so recordings made before
    multi-tab support existed still validate — read-new-fallback-old, same as post_condition."""

    id: str = "tab_0"
    index: int = 0
    # "initial" = the tab the recording started on; "site" = opened via window.open()/target=_blank
    # (Playwright's `page.opener()` resolves it, e.g. a link click); "user" = no opener at all
    # (Ctrl+T or similar) — the runtime must create this tab itself on replay instead of waiting
    # for the site to open it.
    opened_by: Literal["initial", "site", "user"] = "initial"
    opener_tab: str | None = None
    url: str = ""


class Ancestor(BaseModel):
    """One ancestor element in the chain up to <body>."""

    tag: str
    id: str | None = None
    classes: list[str] = Field(default_factory=list)
    outer_html: str = ""  # truncated by bridge.js to keep payloads bounded


class SnapshotRef(BaseModel):
    """Pointer to a deduplicated DOM+a11y blob captured at compile time."""

    ref: str = ""  # uuid assigned by session.py on first capture of this hash
    dom_hash: str = ""  # sha256 of full HTML
    a11y_path: str | None = None  # relative blob path
    dom_path: str | None = None   # relative blob path


class RecordedEvent(BaseModel):
    """Single user action with all attached signals (paths, not bytes)."""

    action: ActionMeta
    target: TargetDom
    selectors: Selectors
    context: DomContext
    semantic: SemanticFeatures
    anchors: list[AnchorRelation] = Field(default_factory=list)
    visual: VisualFeatures
    page: PageContext
    state_change: StateChange
    timing: Timing
    extras: dict[str, Any] = Field(default_factory=dict)
    frame: FrameContext = Field(default_factory=FrameContext)
    tab: TabContext = Field(default_factory=TabContext)

    # Optional — absent on recordings made before these existed (read-new-fallback-old).
    post_condition: PostCondition | None = None

    # Conditional-state observation (recording-next-steps.md Priority 2): set when this event's
    # target sat inside an optional interstitial (dialog / cookie-consent banner) — observed by
    # bridge.js::detectOptionalContainer, never probed for. Purely advisory: the compiler leaves
    # the step a normal required linear step regardless; only a human confirming in Human Edit
    # converts it to a try_dismiss branch (see Key Invariants: "branch steps compile only from
    # observed states + human confirmation").
    optionality: Literal["stochastic"] | None = None
    branch_hint: dict[str, Any] | None = None

    # Custom date-picker detection (see DateContext). Optional — absent on recordings made
    # before this existed, and on every non-calendar click regardless of recording age.
    date_context: DateContext | None = None

    # Multiple-choice control detection (see ChoiceContext). Optional — absent on recordings made
    # before this existed, and on every non-MCQ action regardless of recording age.
    choice_context: ChoiceContext | None = None
    dialog_context: DialogContext | None = None

    # Phase 2: compile-time signals for LLM-based selector generation (REQUIRED).
    # Recordings without these cannot validate; must be re-recorded.
    ancestors: list[Ancestor]
    surrounding_text: str
    snapshot: SnapshotRef
