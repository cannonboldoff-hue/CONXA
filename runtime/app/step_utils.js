"use strict";
// Shared low-level accessors for runtime step objects, extracted from run.js.
// Used by every extracted seam below it (resolution → assertions/locators →
// handlers → cascade), so this module stays dependency-free on purpose.
function unique(values) {
  return Array.from(new Set(values.filter(Boolean)));
}

function asObject(value) {
  return value && typeof value === "object" ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

// EXEC-24 — step types whose action cannot be safely dispatched twice. A second click submits
// twice, a second upload uploads twice, a second checkbox toggle undoes the first. Everything
// NOT listed here (fill, type, select, focus, hover, scroll, …) is idempotent in practice:
// re-running it lands the page in the same state, so recovery re-dispatches it freely.
// Lives here, in the dependency-free leaf, because both locators.js and cascade.js need it and
// cascade.js already depends on locators.js.
const NON_IDEMPOTENT_STEP_TYPES = new Set([
  "click", "dblclick", "right_click", "repeat_click",
  "keyboard_shortcut", "upload", "drag_drop",
  "set_checkbox", "set_radio",
]);

function isNonIdempotent(step) {
  const s = asObject(step);
  if (NON_IDEMPOTENT_STEP_TYPES.has(s.type)) return true;
  // date_pick is a special case, not a blanket type-set entry: a NATIVE <input type=date> re-fill
  // is a genuine no-op (idempotent), but a custom-calendar grid drive
  // (handler_hints.control_kind === "date_picker") re-clicks the field's own open toggle guarded
  // only by an isVisible() check — a re-drive on a toggle-style widget can land the picker
  // CLOSED instead of open, the opposite of what the retry expects. See date_picker.js.
  if (s.type === "date_pick") return asObject(s.handler_hints).control_kind === "date_picker";
  return false;
}

module.exports = { unique, asObject, asArray, NON_IDEMPOTENT_STEP_TYPES, isNonIdempotent };
