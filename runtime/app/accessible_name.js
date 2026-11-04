"use strict";
// The accessible name of a recorded element, derived from its fingerprint. ONE function for the two
// places that need it (resolver.js scoring, cascade.js a11y recovery). Python twin:
// conxa_compile/compiler/identity_bundle.py::_accessible_name, held together by
// test/fixtures/fingerprint_name_cases.json (run by both languages) and accessible_name_cases.json
// (the keyboard-shortcut tail).
//
// Order: aria_label, alt, title, placeholder, visible text (only for roles that are named from their
// content, and only when it is short enough to be a name), nearby <label> text (only for form controls).
// `name` is the HTML form-field attribute, never an ARIA accessible-name source, so it is not used.
// label_text is gated on form controls because for a link or button it is surrounding context and can
// point at a neighbour (a link's label_text mis-captured as "Project" would recover the WRONG element).

// A control named by its label is a form control under either its tag or its role.
const LABELLED_TAGS = new Set([
  "input", "select", "textarea",
  "textbox", "searchbox", "combobox", "listbox", "spinbutton", "checkbox", "radio",
]);

// ARIA "name from content" roles. A combobox/listbox/textbox/searchbox/spinbutton/bare <select> never
// gets its name from its own text (concatenated <option> text is not a name any browser computes), so
// visible text is not a name candidate for those. Mirrors identity_bundle.py::_NAME_FROM_CONTENT_ROLES.
const NAME_FROM_CONTENT_ROLES = new Set([
  "button", "link", "heading", "cell", "gridcell", "columnheader", "rowheader",
  "checkbox", "radio", "menuitem", "menuitemcheckbox", "menuitemradio",
  "option", "tab", "treeitem", "switch", "tooltip",
]);

// Visible text longer than this is a paragraph, not a name. Pinned by the "text over 80 characters"
// row of fingerprint_name_cases.json; identity_bundle.py uses the same number.
const MAX_NAME_CHARS = 80;

// Drive (and similar) append "Alt+C then U" to the visible label; Playwright's accessible name does not.
const SHORTCUT_TAIL_RE = /\s+(?:(?:Ctrl|Control|Alt|Shift|Cmd|Command|Meta|Win|Windows|Option)\s*\+\s*)+[A-Za-z0-9](?:\s+then\s+(?:(?:(?:Ctrl|Control|Alt|Shift|Cmd|Command|Meta|Win|Windows|Option)\s*\+\s*)+[A-Za-z0-9]|[A-Za-z0-9]))*\s*$/i;

function stripKeyboardShortcutTail(text) {
  return String(text || "").replace(SHORTCUT_TAIL_RE, "").trim();
}

function fingerprintName(fingerprint) {
  const fp = fingerprint && typeof fingerprint === "object" ? fingerprint : {};
  const tag = String(fp.tag || "").toLowerCase();
  const role = String(fp.role || "").toLowerCase();
  const innerText = String(fp.inner_text || "").trim();
  const fromContent = (NAME_FROM_CONTENT_ROLES.has(tag) || NAME_FROM_CONTENT_ROLES.has(role)) && innerText.length <= MAX_NAME_CHARS
    ? innerText : "";
  const fromLabel = LABELLED_TAGS.has(tag) || LABELLED_TAGS.has(role) ? fp.label_text : "";
  return stripKeyboardShortcutTail(String(
    fp.aria_label || fp.alt || fp.title || fp.placeholder || fromContent || fromLabel || "",
  ).trim());
}

module.exports = { fingerprintName, stripKeyboardShortcutTail, LABELLED_TAGS, NAME_FROM_CONTENT_ROLES, MAX_NAME_CHARS };
