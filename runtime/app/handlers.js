"use strict";
// Action dispatch seam, extracted from run.js: the HANDLERS table, branch
// primitives (best-effort by invariant — never enter the recovery cascade),
// executeStep, recovery embedding, and agent-override injection.
const pageScripts = require("./page_scripts");
const { interpolate } = require("./interpolate");
const { evalOn, countOn, EVAL_TIMED_OUT } = require("./page_eval");
const {
  PAGE_LOAD_TIMEOUT_MS,
  ACTION_TIMEOUT_MS,
  SECONDARY_ACTION_TIMEOUT_MS,
  DOWNLOAD_WAIT_TIMEOUT_MS,
  UPLOAD_SETTLE_TIMEOUT_MS,
  DIALOG_WAIT_TIMEOUT_MS,
} = require("./run_config");
const { asObject, asArray, unique } = require("./step_utils");
const { pollPositive } = require("./assertions");
const fs = require("fs");
const { DOWNLOAD_ONLY_PLACEHOLDER_RE, resolveUploadPaths } = require("./uploads");
const {
  locatorCandidates,
  resolveStep,
  rootCandidates,
  gateLocator,
} = require("./resolution");
const {
  parseDateInput,
  formatForDisplay,
  monthDelta,
  machineCellSelector,
  dayNumberSelector,
  monthLabel,
  MAX_NAV_CLICKS,
} = require("./date_picker");
const { matchOption, matchOptions, optionsSummary } = require("./choice");
const {
  PRIMARY,
  markMayHaveActed,
  withLocator,
  withLocatorPair,
  runLocatorStep,
  hasTarget,
  clickFirst,
  checkboxValue,
  walkHoverChain,
  parseDragSelectors,
  parseKeyboardShortcut,
  baseSelector,
  stepWithSelector,
} = require("./locators");

// tab_open/tab_switch/popup are NOT here (see tabs.js): the tab switch they mark already
// happened via resolveStepPage() before executeStep() runs for any step, including these
// markers, so their own handlers really are empty — but they're declared explicitly below,
// not folded into this blanket list, so "no-op step type" isn't read as "nothing happens
// around this step" for the one category where something very much does.
const NOOP_STEP_TYPES = [
  "frame_enter", "frame_exit",
  // "upload_intent" (native-OS-file-picker provenance) never actually reaches this dispatch as
  // its own type in a real skill pack — skill_package_builder_saved_skill.py's
  // _saved_step_to_execution_step collapses it to type "upload" (real handler below) at build
  // time, same value/selector. Kept here only as defensive dead code in case that collapsing
  // rule ever regresses; don't read it as "upload_intent uploads are a no-op" — they aren't.
  "upload_intent", "dialog_appeared",
  "file_chooser_opened", "clipboard_copy", "clipboard_paste",
  // dialog_accept/dialog_dismiss are NOT here — see their real handlers below. Without one,
  // a live alert/confirm/prompt during replay falls back to Playwright's own default (silent
  // auto-DISMISS), the opposite of what a recorded "accept" step expects.
];

// Branch primitives (if_present, try_dismiss, wait_for_one_of): probe an element's presence
// without ever throwing, so callers stay outside the recovery cascade (recoverStep only fires
// on a throw escaping runPlan's per-step try — see Key Invariants). probeSpec is a step-shaped
// object: identity_bundle.signals take priority (resolved like a real target), else a plain
// selector/css_selector/target.css string. Polls up to timeoutMs via the existing pollPositive.
async function probePresent(page, probeSpec, inputs, timeoutMs) {
  const spec = asObject(probeSpec);
  const budget = Math.max(0, Number(timeoutMs) || 0);
  const bundle = asObject(spec.identity_bundle);
  if (asArray(bundle.signals).some(s => s && s.selector)) {
    return pollPositive(async () => {
      try { await resolveStep(page, spec, inputs); return true; } catch (_) { return false; }
    }, budget);
  }
  const selector = baseSelector(spec, inputs);
  if (!selector) return false;
  const candidates = await locatorCandidates(page, spec, inputs, selector);
  if (!candidates.length) return false;
  return pollPositive(async () => {
    for (const locator of candidates) {
      try {
        const n = await countOn(locator);
        if (n !== EVAL_TIMED_OUT && n > 0) return true;
      } catch (_) {}
    }
    return false;
  }, budget);
}

// Interactive handlers (click/fill/...) always resolve PRIMARY via identity_bundle.signals and
// throw immediately if none exist (withLocator never falls back to a bare selector unless
// _explicit_selector is set — see withLocator). Branch bodies are hand-authored/foundation-scope
// steps that typically carry only a plain selector, so force string mode exactly like recovery's
// stepWithSelector does, or the nested action would always fail with "pack must be recompiled".
function resolvableBranchStep(step, inputs) {
  if (!step || step._explicit_selector) return step;
  if (asArray(asObject(step.identity_bundle).signals).some(s => s && s.selector)) return step;
  const selector = baseSelector(step, inputs);
  return selector ? stepWithSelector(step, selector) : step;
}

// Multiple-choice control kind (radio/select/aria_radio/aria_listbox/checkbox-group) — see
// CLAUDE.md's multiple-choice plan and compiler/choice.py, which produces this shape.
// handler_hints.choice is absent on every step compiled before this feature existed, so every
// choice branch below is additive: an old compiled skill falls straight through to its original,
// unchanged behavior.
function _choiceHints(step) {
  const hints = asObject(step.handler_hints);
  return hints.control_kind === "choice" ? asObject(hints.choice) : null;
}

// How long to wait for a popup listbox to render its options after clicking its opener. Short:
// this is a local UI toggle, not a network round-trip, and a miss falls through to the ordinary
// locator wait below rather than failing here.
const CHOICE_MENU_OPEN_TIMEOUT_MS = 2000;

/** Click a popup listbox's opener when its options are not already on the page.
 *
 * Deterministic and zero-LLM (Tier 1): the opener is a compile-time recorded selector, not a
 * guess. A no-op when the menu is already open, when the recording captured no opener (an
 * always-visible group, or a skill compiled before openers were recorded), or when the opener
 * cannot be clicked — in every one of those cases the caller's own locator wait is still the
 * thing that decides success or failure, so this never converts a real miss into a false pass.
 */
async function _ensureChoiceMenuOpen(page, choice, option) {
  const opener = String((choice && choice.opener_selector) || "").trim();
  if (!opener || !option || !option.selector) return;
  try {
    const n = await countOn(page.locator(option.selector));
    if (n !== EVAL_TIMED_OUT && n > 0) return; // already open
    await page.locator(opener).first().click({ timeout: SECONDARY_ACTION_TIMEOUT_MS });
    await page.locator(option.selector).first()
      .waitFor({ state: "attached", timeout: CHOICE_MENU_OPEN_TIMEOUT_MS });
  } catch (_) {
    // Leave the outcome to the caller's locator step.
  }
}

// Single-option choice (radio/select/aria_radio/aria_listbox): resolve the caller's value against
// the recorded option set, or fail closed naming every valid option — never fall back to
// whichever option happened to be recorded, which would silently submit a wrong answer.
function _resolveChoiceOption(step, inputs, choice) {
  const userValue = interpolate(step.value || "", inputs);
  const option = matchOption(userValue, choice.options);
  if (!option) {
    // badInput (see run.js's primaryErr.badInput check, and locators.js's identical existing
    // upload-handler pattern): no amount of re-resolving the element fixes a value that doesn't
    // match any recorded option -- fail straight through instead of burning Tier 3+ LLM recovery
    // on a mistake this message already explains.
    throw Object.assign(
      new Error(`Input "${step.input_binding}" = "${userValue}" is not one of: ${optionsSummary(choice.options)}`),
      { badInput: true },
    );
  }
  return { option, userValue };
}

// Minimal CSS attribute-value escape for the native-radio fallback selector below — only needs
// to survive being placed inside a double-quoted [attr="..."] selector.
function _cssAttrEscape(value) {
  return String(value == null ? "" : value).replace(/[\\"]/g, "\\$&");
}

// Runs a branch body best-effort: each nested step's own failure is swallowed so the branch
// never escalates to recovery — a failed cookie-banner dismissal should not burn a paid Tier
// 3/4 recovery cycle. Nested steps use the same flat runtime step shape as top-level steps.
async function runBranchBody(page, steps, inputs, ctx) {
  for (const nested of asArray(steps)) {
    if (!nested || typeof nested !== "object") continue;
    try {
      await executeStep(page, resolvableBranchStep(nested, inputs), inputs, ctx);
    } catch (_) {
      // best-effort — do not propagate; branch bodies never enter Tier 1-4 recovery.
    }
  }
}

// --- date_pick adapter: the Playwright-touching half of date_picker.js's pure math -----------
// See CLAUDE.md's date-picker plan. Typed-first (works for native <input type=date> and most
// custom widgets outright); grid fallback only for a compiled custom-calendar step
// (handler_hints.control_kind === "date_picker") whose typed attempt didn't stick.

function _pad2(n) {
  return String(n).padStart(2, "0");
}

function _isoFor(parsed) {
  const datePart = `${parsed.year}-${_pad2(parsed.month)}-${_pad2(parsed.day)}`;
  return parsed.hour != null ? `${datePart}T${_pad2(parsed.hour)}:${_pad2(parsed.minute)}` : datePart;
}

// Mirrors assertions.js's own value_equals tolerance (normalized-exact, else "field contains
// expected"). This IS the post-condition for a native/anchored-field date_pick — there is no
// compile-time value_equals assertion behind it (date_picker.py never emits one; a literal
// recorded date would fail VERIFY for every caller-supplied date other than the one recorded),
// so a false result here must fail the step, not just steer the typed-vs-grid decision.
//
// trustIsoEquality defaults true for native <input type=date>, where fill()'s value IS raw ISO
// — a real signal the browser accepted it. For a custom widget (isCustomPicker at the call site)
// with no compiled display_format, raw ISO text landing back in the field proves nothing: it's
// just our own literal fill() echoed unparsed, not evidence the widget registered that date (see
// handlers.js's date_pick handler for the jQuery-UI-shaped case this was found against — a plain
// text field that happily holds any string typed into it). Callers for that case pass false so an
// unverifiable typed attempt correctly falls through to the grid strategy instead of a false pass.
function _dateValueMatches(actual, parsed, displayFormat, { trustIsoEquality = true } = {}) {
  const normalized = String(actual || "").trim().toLowerCase();
  if (!normalized) return false;
  if (trustIsoEquality && normalized === _isoFor(parsed).toLowerCase()) return true;
  const formatted = formatForDisplay(parsed, displayFormat || "");
  if (!formatted) return false;
  const normFormatted = formatted.toLowerCase();
  return normalized === normFormatted || normalized.includes(normFormatted);
}

// Every other handler acts through withLocator, which gates via gateLocator() before touching
// the page (visible -> RAF-stable -> enabled). The calendar-grid driver below acts directly on
// root.locator(...) instead, since it isn't walking the identity-bundle resolution path — but
// jQuery UI (and calendar widgets generally) fully replace the header+day-grid DOM on every nav
// click, so an un-gated click can race that replace. Reuse the same gate here rather than
// inventing a second stability mechanism.
async function _gatedClick(locator, step, timeout) {
  await gateLocator(locator, step).catch(() => {});
  await locator.click({ timeout });
}

// Drives one root's calendar grid to the target date: open (unless the grid's already up, as on
// a range's second leg) -> nav to the target month, bounded and boundary-aware -> click the day
// cell, preferring a machine-readable attribute rebuilt for THIS date over the recorded (and only
// ever valid for the recording's own day) cell selector -> optional time option for a datetime.
async function _driveDatePickerGrid(root, parsed, hints, step) {
  if (hints.role !== "range_end" && hints.open) {
    const alreadyOpen = hints.grid
      ? await root.locator(hints.grid).first().isVisible().catch(() => false)
      : false;
    if (!alreadyOpen) {
      await _gatedClick(root.locator(hints.open).first(), step, ACTION_TIMEOUT_MS);
    }
  }
  if (hints.grid) {
    await root.locator(hints.grid).first().waitFor({ state: "visible", timeout: ACTION_TIMEOUT_MS });
  }

  if (hints.year_select || hints.month_select) {
    // react-datepicker's showMonthDropdown/showYearDropdown mode and equivalents (MUI's, Ant
    // Design's year-picker views) — month/year navigation is a pair of native <select>s, not
    // click-through prev/next buttons. selectOption by LABEL, never by value: the option's raw
    // value isn't standardized across libraries (react-datepicker's month value is 0-indexed;
    // others use 1-indexed or the month name itself), but every one of them renders a real
    // English month name / year number as the visible option text.
    if (hints.year_select) {
      await root.locator(hints.year_select).first()
        .selectOption({ label: String(parsed.year) }, { timeout: SECONDARY_ACTION_TIMEOUT_MS })
        .catch(() => {}); // best-effort nav — the day-cell click below is what actually throws on failure
    }
    if (hints.month_select) {
      const label = monthLabel(parsed.month);
      if (label) {
        await root.locator(hints.month_select).first()
          .selectOption({ label }, { timeout: SECONDARY_ACTION_TIMEOUT_MS })
          .catch(() => {});
      }
    }
  } else if (hints.header && (hints.next || hints.prev)) {
    for (let i = 0; i < MAX_NAV_CLICKS; i++) {
      const headerText = await root.locator(hints.header).first()
        .innerText({ timeout: SECONDARY_ACTION_TIMEOUT_MS }).catch(() => "");
      const delta = monthDelta(headerText, parsed.year, parsed.month);
      if (delta === null || delta === 0) break; // unparseable header, or already on target month
      const navSelector = delta > 0 ? hints.next : hints.prev;
      if (!navSelector) break;
      await _gatedClick(root.locator(navSelector).first(), step, SECONDARY_ACTION_TIMEOUT_MS);
      // A disabled min/max boundary means this click did nothing — stop rather than loop until
      // MAX_NAV_CLICKS on a header that will never reach the target.
      const after = await root.locator(hints.header).first()
        .innerText({ timeout: SECONDARY_ACTION_TIMEOUT_MS }).catch(() => "");
      if (after === headerText) break;
    }
  }

  const cellScope = hints.grid ? root.locator(hints.grid) : root;
  const machineSelector = machineCellSelector(hints.cell_attr, _isoFor(parsed).slice(0, 10));
  let cellClicked = false;
  if (machineSelector) {
    try {
      await _gatedClick(cellScope.locator(machineSelector).first(), step, SECONDARY_ACTION_TIMEOUT_MS);
      cellClicked = true;
    } catch (_) { /* fall through to the day-number strategy */ }
  }
  if (!cellClicked) {
    await _gatedClick(cellScope.locator(dayNumberSelector(parsed.day)).first(), step, SECONDARY_ACTION_TIMEOUT_MS);
  }

  if (hints.kind === "datetime" && hints.time_option) {
    await cellScope.locator(`:text-is(${JSON.stringify(hints.time_option)})`).first()
      .click({ timeout: SECONDARY_ACTION_TIMEOUT_MS })
      .catch(() => {}); // best-effort — the day-cell click above already landed the date itself
  }
}

async function _runDatePickerGrid(page, step, inputs, parsed, hints) {
  const roots = await rootCandidates(page, step, inputs);
  if (!roots.length) {
    throw new Error("date_pick: containing frame could not be located");
  }
  let lastErr = null;
  for (const root of roots) {
    try {
      await _driveDatePickerGrid(root, parsed, hints, step);
      return;
    } catch (err) {
      lastErr = err;
    }
  }
  throw markMayHaveActed(lastErr || new Error("date_pick: calendar grid could not be driven"));
}

// Shared by the check/assert handlers below — see their comment for why they're one function.
async function _evaluateCheckLikeStep(page, step, inputs) {
  const kind = String(step.kind || "").toLowerCase();
  const timeout = Number(step.timeout) || SECONDARY_ACTION_TIMEOUT_MS;

  if (kind === "url") {
    const pattern = interpolate(step.pattern || "", inputs);
    if (!pattern) throw new Error(`${step.type} step has no URL pattern to check`);
    let re;
    try { re = new RegExp(pattern); } catch (e) {
      throw new Error(`${step.type} step's URL pattern isn't a valid pattern: ${pattern}`);
    }
    const ok = await pollPositive(() => re.test(page.url()), timeout);
    if (!ok) throw new Error(`URL check failed: ${page.url()} does not match ${pattern}`);
    return;
  }

  if (kind === "url_exact") {
    const url = interpolate(step.url || "", inputs);
    if (!url) throw new Error(`${step.type} step has no exact URL to check`);
    const ok = await pollPositive(() => page.url() === url, timeout);
    if (!ok) throw new Error(`URL check failed: ${page.url()} is not ${url}`);
    return;
  }

  if (kind === "selector") {
    const selector = interpolate(step.selector || "", inputs);
    if (!selector) throw new Error(`${step.type} step has no element selector to check`);
    await withLocator(page, step, inputs, selector, timeout, async locator => locator.first());
    return;
  }

  if (kind === "text") {
    const selector = interpolate(step.selector || "", inputs);
    const expected = interpolate(step.text || "", inputs);
    if (!selector) throw new Error(`${step.type} step has no element selector to read text from`);
    if (!expected) throw new Error(`${step.type} step has no expected text to check for`);
    let actual;
    try {
      actual = await withLocator(page, step, inputs, selector, timeout, locator =>
        locator.first().innerText({ timeout }));
    } catch (e) {
      throw new Error(`${step.type} text: element not found (${selector})`);
    }
    if (!actual.includes(expected)) {
      throw new Error(`${step.type} text: "${actual}" does not include "${expected}"`);
    }
    return;
  }

  // "snapshot" is not implemented at runtime (no visual-diff engine here) and anything else is
  // a compiler/runtime version mismatch — either way, refuse rather than silently passing.
  throw new Error(`${step.type} step has an unsupported check type "${kind || "(none)"}" — republish this skill`);
}

const HANDLERS = {
  wait: async (page, step) => {
    await page.waitForTimeout(Math.min(Number(step.ms) || 250, 1000));
  },

  navigate: async (page, step, inputs) => {
    await page.goto(interpolate(step.url || "", inputs), { timeout: PAGE_LOAD_TIMEOUT_MS, waitUntil: "domcontentloaded" });
  },

  // Browser Back/Forward recorded by the recorder's CDP navigation-history tracking.
  // The dispatched `page` is already the resolved target tab (resolveStepPage ran before
  // executeStep), so history navigation replays on exactly the tab where it was performed —
  // never rewritten to a guessed URL. step.url carries the URL the recorder observed AFTER
  // the navigation; it's informational only (editor display / debugging), not navigated to.
  browser_back: async (page) => {
    await page.goBack({ timeout: PAGE_LOAD_TIMEOUT_MS, waitUntil: "domcontentloaded" });
  },

  browser_forward: async (page) => {
    await page.goForward({ timeout: PAGE_LOAD_TIMEOUT_MS, waitUntil: "domcontentloaded" });
  },

  scroll: async (page, step, inputs) => {
    if (hasTarget(step, inputs)) {
      await withLocator(page, step, inputs, PRIMARY, 0, async locator => {
        await locator.first().scrollIntoViewIfNeeded({ timeout: SECONDARY_ACTION_TIMEOUT_MS });
      }).catch(() => {});
    } else {
      const deltaX = Number(step.delta_x) || 0;
      const deltaY = Number(step.delta_y) || 0;
      await evalOn(page, pageScripts.scrollBy, [deltaX, deltaY]);
    }
  },

  fill: async (page, step, inputs) => {
    await runLocatorStep(page, step, inputs, locator => {
      return locator.fill(interpolate(step.value || "", inputs), { timeout: ACTION_TIMEOUT_MS });
    });
  },

  type: async (page, step, inputs) => {
    await runLocatorStep(page, step, inputs, locator => {
      return locator.fill(interpolate(step.value || "", inputs), { timeout: ACTION_TIMEOUT_MS });
    });
  },

  click: async (page, step, inputs) => {
    await walkHoverChain(page, step, inputs);
    await withLocator(page, step, inputs, PRIMARY, 0, async locator => {
      await clickFirst(locator, { timeout: ACTION_TIMEOUT_MS });
    });
  },

  dblclick: async (page, step, inputs) => {
    await runLocatorStep(page, step, inputs, locator => {
      return locator.dblclick({ timeout: ACTION_TIMEOUT_MS });
    });
  },

  right_click: async (page, step, inputs) => {
    await runLocatorStep(page, step, inputs, locator => {
      return locator.click({ button: "right", timeout: ACTION_TIMEOUT_MS });
    });
  },

  hover: async (page, step, inputs) => {
    await runLocatorStep(page, step, inputs, locator => {
      return locator.hover({ timeout: SECONDARY_ACTION_TIMEOUT_MS });
    });
  },

  select: async (page, step, inputs) => {
    const choice = _choiceHints(step);
    if (choice) {
      const { option, userValue } = _resolveChoiceOption(step, inputs, choice);
      // A native <select>'s own option VALUES are library/site-specific and unguessable (see
      // date_picker.js's monthLabel for the identical reasoning on month dropdowns); every
      // library still renders a human-readable label, so selecting by label sidesteps that.
      await runLocatorStep(page, step, inputs, locator => {
        return locator.selectOption({ label: option.label }, { timeout: ACTION_TIMEOUT_MS });
      });
      return;
    }
    await runLocatorStep(page, step, inputs, locator => {
      return locator.selectOption(interpolate(step.value || "", inputs), { timeout: ACTION_TIMEOUT_MS });
    });
  },

  select_option: async (page, step, inputs) => {
    const choice = _choiceHints(step);
    // An ARIA listbox item (kind "aria_listbox") is a plain element with role="option", not a
    // native <select> -- .selectOption() only works on the latter. Click the matched option's
    // own recorded selector instead, the same way set_radio acts on an ARIA radio's own element.
    if (choice && choice.kind === "aria_listbox") {
      const { option } = _resolveChoiceOption(step, inputs, choice);
      // A popup listbox's options exist only while its menu is open, and the click that opens it
      // is not a recorded step (it lands on a role-less container the recorder discards as noise).
      // Open it here when it isn't already, or the option below can only ever miss.
      await _ensureChoiceMenuOpen(page, choice, option);
      const targetStep = stepWithSelector(step, option.selector);
      await runLocatorStep(page, targetStep, inputs, locator => {
        return locator.click({ timeout: ACTION_TIMEOUT_MS });
      });
      return;
    }
    await HANDLERS.select(page, step, inputs);
  },

  focus: async (page, step, inputs) => {
    if (hasTarget(step, inputs)) {
      await withLocator(page, step, inputs, PRIMARY, 0, async locator => {
        const first = locator.first();
        try {
          await first.click({ timeout: SECONDARY_ACTION_TIMEOUT_MS });
        } catch (_) {
          await first.focus({ timeout: SECONDARY_ACTION_TIMEOUT_MS }).catch(() => {});
        }
      });
    }
  },

  set_checkbox: async (page, step, inputs) => {
    const choice = _choiceHints(step);
    // Every compiled checkbox-GROUP choice is multi (compiler/choice.py::collapse_choice_group_runs
    // always sets multi=true) -- a standalone checkbox ("I agree") never gets a choice_context at
    // all (bridge.js requires >= 2 group members), so it always falls through to today's behavior
    // below unchanged.
    if (choice && choice.multi) {
      // Read the raw input directly rather than through interpolate(): a checkbox group's answer
      // is a LIST, and interpolate() only substitutes {{var}} inside a string template.
      const rawValue = step.input_binding ? inputs[step.input_binding] : undefined;
      const picked = matchOptions(rawValue, choice.options);
      if (picked === null) {
        throw Object.assign(
          new Error(
            `Input "${step.input_binding}" = ${JSON.stringify(rawValue)} is not a valid subset of: ${optionsSummary(choice.options)}`,
          ),
          { badInput: true },
        );
      }
      const pickedValues = new Set(picked.map(o => o.value));
      for (const opt of choice.options) {
        if (!opt.selector) continue;
        const checked = pickedValues.has(opt.value);
        await runLocatorStep(page, stepWithSelector(step, opt.selector), inputs, locator => {
          return locator.setChecked(checked, { timeout: ACTION_TIMEOUT_MS });
        });
      }
      return;
    }
    await runLocatorStep(page, step, inputs, locator => {
      return locator.setChecked(checkboxValue(step, inputs), { timeout: ACTION_TIMEOUT_MS });
    });
  },

  set_radio: async (page, step, inputs) => {
    const choice = _choiceHints(step);
    if (choice) {
      const { option } = _resolveChoiceOption(step, inputs, choice);
      // Recorded per-option selector first; a group_key + value fallback for the rare case a
      // recorded selector no longer resolves (mirrors identity_bundle's own primary+fallback
      // pattern, scoped to what a radio group actually needs: name+value is a stable enough
      // native-HTML identity that doesn't depend on any one selector engine).
      const fallback = choice.group_key
        ? `input[type="radio"][name="${_cssAttrEscape(choice.group_key)}"][value="${_cssAttrEscape(option.value)}"]`
        : "";
      const selector = option.selector || fallback;
      if (!selector) throw new Error(`No selector available for option "${option.label || option.value}"`);
      await runLocatorStep(page, stepWithSelector(step, selector), inputs, locator => {
        return locator.click({ timeout: ACTION_TIMEOUT_MS });
      });
      return;
    }
    await runLocatorStep(page, step, inputs, locator => {
      return locator.click({ timeout: ACTION_TIMEOUT_MS });
    });
  },

  date_pick: async (page, step, inputs) => {
    const value = interpolate(step.value || "", inputs);
    const hints = asObject(asObject(step.handler_hints).date_picker);
    const parsed = parseDateInput(value, hints.display_format);
    const isCustomPicker = asObject(step.handler_hints).control_kind === "date_picker";
    // A plain text field the user typed a date into during recording (compiler/date_picker.py::
    // convert_typed_date_steps): fill the app's own recorded format, no calendar to fall back to.
    const isTextField = hints.kind === "text_field";

    // The step learned this application's date format at compile time, so a value that can't be
    // read as a date is the caller's mistake — say so instead of typing garbage into the field.
    if (!parsed && hints.display_format) {
      throw Object.assign(
        new Error(`Input "${step.input_binding}" = ${JSON.stringify(value)} is not a valid date; send one like 2026-09-25`),
        { badInput: true },
      );
    }

    // Unparseable value: native <input type=month|week|time> never produces a full ISO
    // date/datetime (bridge.js records their raw "2026-09"/"2026-W38"/"14:30" as-is), so
    // date_picker.js correctly refuses to parse them. Original unconditional fill/click, exactly
    // as before this feature existed — nothing below this branch ever applied to these types.
    if (!parsed) {
      await runLocatorStep(page, step, inputs, async locator => {
        try {
          await locator.fill(value, { timeout: ACTION_TIMEOUT_MS });
        } catch (_) {
          await locator.click({ timeout: SECONDARY_ACTION_TIMEOUT_MS }).catch(() => {});
        }
      });
      return;
    }

    // Typed-first: covers native <input type=date|datetime-local> unconditionally (their fill
    // format IS the ISO value, and handler_hints.date_picker.display_format is always empty for
    // them) and most custom widgets outright, since many accept direct typed input even when the
    // recording drove the grid to produce it.
    const displayValue = formatForDisplay(parsed, hints.display_format || "") || _isoFor(parsed);
    let typedOk = false;
    // grid_only (an inline always-visible calendar, no anchored field — see date_picker.py) has
    // nothing to type into at all: the step's own compiled target is the day cell itself, so a
    // typed attempt would only burn the full action timeout on a guaranteed-to-fail fill().
    if (!(isCustomPicker && hints.strategy === "grid_only")) {
      try {
        await runLocatorStep(page, step, inputs, async locator => {
          await locator.fill(displayValue, { timeout: ACTION_TIMEOUT_MS });
          // Enter is skipped for a custom picker: focusing the field to fill() it can open a
          // popup calendar as a side effect (e.g. jQuery UI's showOn:'focus'), and Enter on an
          // OPEN popup calendar finalizes/closes it right there — confirmed live against
          // jqueryui.com's own datepicker demo, where that close (and the grid fallback's
          // immediate re-open right after) races the widget's own fade animation queue and can
          // leave the calendar getting yanked shut mid-click several actions later, well past
          // this step. A native input has no popup to corrupt, so it keeps pressing Enter for
          // widgets that need it to commit.
          if (!isCustomPicker && !isTextField) {
            try { await locator.press("Enter", { timeout: SECONDARY_ACTION_TIMEOUT_MS }); } catch (_) { /* not every widget commits on Enter */ }
          }
          let actual = "";
          try { actual = await locator.inputValue({ timeout: SECONDARY_ACTION_TIMEOUT_MS }); } catch (_) { /* not every widget is a real <input> */ }
          typedOk = _dateValueMatches(actual, parsed, hints.display_format, { trustIsoEquality: !isCustomPicker && !isTextField });
        });
      } catch (err) {
        if (!isCustomPicker) throw err; // no grid fallback exists for a native input
        typedOk = false;
      }
    }

    if (typedOk) return;
    if (!isCustomPicker) {
      // Native <input type=date|datetime-local>: fill() didn't throw, but the readback doesn't
      // match what was typed — used to return here as a silent success. There is no grid
      // fallback for a native input, and no compile-time value_equals assertion behind this
      // either (date_pick.py never emits one for the typed-first/native path) — this readback
      // check IS the post-condition, so it must actually fail the step.
      throw markMayHaveActed(new Error(`date_pick: field value after fill does not match "${displayValue}"`));
    }

    // Grid fallback: only for a compiled custom-calendar step whose typed attempt didn't stick —
    // drives the widget open->nav->day-cell (+time) instead of guessing.
    await _runDatePickerGrid(page, step, inputs, parsed, hints);
  },

  drag_drop: async (page, step, inputs) => {
    const { srcSelector, dstSelector } = parseDragSelectors(step, inputs);
    if (srcSelector && dstSelector) {
      await withLocatorPair(page, step, inputs, srcSelector, dstSelector, 0, (srcLoc, dstLoc) => {
        return srcLoc.first().dragTo(dstLoc.first(), { timeout: ACTION_TIMEOUT_MS });
      });
    }
  },

  keyboard_shortcut: async (page, step, inputs) => {
    const keyStr = parseKeyboardShortcut(interpolate(step.value || "", inputs));
    // Dispatches straight at the page, bypassing withLocator's seam — so it carries its own
    // EXEC-24 mark. A keystroke that may have landed (Ctrl+S, Enter on a form) must not be
    // blindly re-sent by the recovery cascade.
    if (keyStr) {
      try {
        await page.keyboard.press(keyStr, { delay: 50 });
      } catch (err) {
        throw markMayHaveActed(err);
      }
    }
  },

  // check/assert compile to the identical {kind, pattern|url|selector|text} shape via
  // _saved_check_like_step (skill_package_builder_saved_skill.py) — "check" and "assert" are two
  // step types only for the editor's own UI wording; the runtime evaluates them the same way.
  // An unrecognized kind is a compiler/runtime version mismatch, never a silent pass.
  check: (page, step, inputs) => _evaluateCheckLikeStep(page, step, inputs),
  assert: (page, step, inputs) => _evaluateCheckLikeStep(page, step, inputs),

  screenshot: async (page) => {
    await page.screenshot({ type: "png", timeout: SECONDARY_ACTION_TIMEOUT_MS }).catch(() => null);
  },

  upload: async (page, step, inputs) => {
    const rawValue = String(step.value || "");
    const resolved = interpolate(rawValue, inputs);
    // Only the compiler-controlled downloaded_files_dir placeholder qualifies for cleanup — a
    // hand-authored {{file_path}} that happens to point at a real directory must never have its
    // files deleted out from under the user. Moved up (was computed just before runLocatorStep
    // below) so the BUILD-31 guard right after can use it too.
    const isSharedDownloadsDir = resolved === inputs.downloaded_files_dir;
    // A bare {{downloaded_file...}}-style placeholder that resolved to "" isn't a missing
    // declared input — filter_runtime_only_inputs (compiler) deliberately never declares these,
    // so telling the user to "supply" one is a dead end. It means the recorded download for this
    // step didn't produce a file during this run (timed out, or the download never fired).
    if (!resolved.trim() && DOWNLOAD_ONLY_PLACEHOLDER_RE.test(rawValue.trim())) {
      throw new Error(
        "upload step has no file path — the recorded download for this step didn't produce a " +
        "file during this run (it may have timed out or never started)",
      );
    }
    // BUILD-31: unlike the single-file downloaded_file* placeholders (bound by the
    // download_observed handler only once a real file exists, and throwing badInput otherwise),
    // server.js sets inputs.downloaded_files_dir unconditionally at run start — it is never the
    // empty string, so the guard above can never fire for it, even when zero files were ever
    // downloaded and the directory doesn't exist. Check for that directly: a run-per-run check,
    // not a bind-time one, since the same placeholder may be read by several upload steps at
    // different points in the run, some before and some after a download actually lands.
    if (isSharedDownloadsDir) {
      let hasFiles = false;
      try {
        // Mirrors uploads.js::resolveUploadPaths's own directory-expansion filter — same
        // withFileTypes: true idiom, no second statSync round-trip per entry.
        hasFiles = fs.readdirSync(resolved, { withFileTypes: true }).some(entry => entry.isFile());
      } catch (_) { hasFiles = false; } // directory doesn't exist — same as "no files"
      if (!hasFiles) {
        throw Object.assign(new Error(
          "upload step has no file path — the recorded download for this step didn't produce a " +
          "file during this run (it may have timed out or never started)",
        ), { badInput: true });
      }
    }
    const filePaths = resolveUploadPaths(resolved);

    await runLocatorStep(page, step, inputs, async locator => {
      // Whether this control takes one file or many is a property of the live page, not of
      // what happened to be picked while recording — so ask the element, which stays correct
      // for packs compiled before this existed and for a site that changes the control later.
      // Only worth a round-trip when more than one file is actually on the table.
      if (filePaths.length > 1) {
        // Unknown (detached, cross-origin, evaluate blocked) stays permissive: let
        // setInputFiles have its say rather than blocking an upload on a failed probe.
        const _acceptsResult = await evalOn(locator, el => el.multiple === true).catch(() => true);
        const acceptsMultiple = _acceptsResult === EVAL_TIMED_OUT ? true : _acceptsResult;
        if (!acceptsMultiple) {
          throw Object.assign(new Error(
            `this upload control accepts only one file, but ${filePaths.length} files were given ` +
            `— pass a single file path instead of a folder`,
          ), { badInput: true });
        }
      }
      return locator.setInputFiles(filePaths, { timeout: ACTION_TIMEOUT_MS });
    });

    // Wait for the page's own upload request(s) to settle before this step (and the run, if
    // this is the last step) is considered done — setInputFiles above only confirms the file
    // was attached, not that any upload to a server finished. Best-effort: some pages never go
    // fully network-idle (websockets, polling, analytics beacons), so this must not fail an
    // upload that otherwise succeeded.
    await page.waitForLoadState?.("networkidle", { timeout: UPLOAD_SETTLE_TIMEOUT_MS }).catch(() => {});

    // EXEC-19: the compiler already guarantees a downloaded file is bound to at most one upload
    // step (FIFO consumption in upload_binding.py), but nothing removed the file from the shared
    // per-run download folder — so a later bulk upload's own downloaded_files_dir scan could
    // still pick up an earlier upload's already-consumed files. Delete them now that they're
    // genuinely uploaded, matching the rule the compiler already enforces logically. Best-effort:
    // a failed delete must not fail an upload that already succeeded.
    if (isSharedDownloadsDir) {
      for (const filePath of filePaths) {
        try { fs.unlinkSync(filePath); } catch (_) { /* best-effort cleanup */ }
      }
    }
  },

  // Optional interstitial handling (cookie/consent banners, session-expired screens, optional
  // MFA, A/B variants) — see Key Invariants: branch bodies are best-effort and never enter the
  // Tier 1-4 recovery cascade, since neither this handler nor runBranchBody ever throws.
  if_present: async (page, step, inputs, ctx) => {
    const timeout = Number(step.timeout_ms) || 1500;
    if (await probePresent(page, step, inputs, timeout)) {
      await runBranchBody(page, step.steps, inputs, ctx);
    }
  },

  try_dismiss: async (page, step, inputs) => {
    const timeout = Number(step.timeout_ms) || 800;
    const candidates = unique([...asArray(step.candidates), baseSelector(step, inputs)]);
    for (const selector of candidates) {
      if (!selector) continue;
      try {
        const probeSpec = { selector };
        if (!(await probePresent(page, probeSpec, inputs, timeout))) continue;
        const locator = (await locatorCandidates(page, probeSpec, inputs, selector))[0];
        if (!locator) continue;
        await locator.first().click({ timeout: SECONDARY_ACTION_TIMEOUT_MS });
        return;
      } catch (_) {
        // best-effort — try the next candidate
      }
    }
    if (step.fallback_escape !== false) {
      await page.keyboard.press("Escape").catch(() => {});
    }
  },

  wait_for_one_of: async (page, step, inputs, ctx) => {
    const timeout = Number(step.timeout_ms) || 5000;
    const options = asArray(step.options);
    let matched = null;
    const found = await pollPositive(async () => {
      for (const option of options) {
        if (option && (await probePresent(page, option, inputs, 0))) {
          matched = option;
          return true;
        }
      }
      return false;
    }, timeout);
    if (found && matched) {
      await runBranchBody(page, matched.steps, inputs, ctx);
      return;
    }
    if (step.required) {
      throw new Error("wait_for_one_of: none of the candidate selectors appeared before timeout");
    }
  },
};

for (const type of NOOP_STEP_TYPES) {
  HANDLERS[type] = async () => {};
}

// The `page` handed to these handlers is already the resolved target tab (resolveStepPage ran
// before executeStep for this step like every other) — there is nothing left to do here.
HANDLERS["tab_open"] = async () => {};
HANDLERS["tab_switch"] = async () => {};
HANDLERS["popup"] = async () => {};

HANDLERS["download_observed"] = async (_page, _step, inputs, ctx) => {
  const queue = ctx && ctx.downloadQueue;
  // Used to silently `return` on every one of these three paths — this step "succeeded" having
  // downloaded nothing, and nothing downstream ever checked. Inside a for_each loop that meant
  // every iteration could report success while quietly downloading some, one, or zero of the
  // files the customer asked for; the final bulk upload only failed if the shared downloads
  // folder ended up COMPLETELY empty, so even a partial miss uploaded happily. `badInput: true`
  // is the same short-circuit for_each's own max_iterations check uses (run.js) — it routes
  // straight to stepFailure, skipping the Tier 1-4 recovery cascade entirely, because no amount
  // of re-finding a selector can produce a download that never fired; there is no selector here
  // to re-find (download_observed compiles with recovery.max_attempts=0, same as every other
  // marker action — action_policy.py's NO_RECOVERY_ACTION_TYPES).
  const noDownload = (why) => {
    throw Object.assign(
      new Error(`Expected a file download at this step, but ${why}.`),
      { badInput: true },
    );
  };
  if (!queue) noDownload("no download tracking was set up for this run");
  // server.js's `page.on("download", ...)` listener only pushes onto this queue once Playwright's
  // download event actually fires — which can trail the triggering click by real wall-clock time
  // (server round-trip, header negotiation). Checking the queue once and bailing when it's still
  // empty raced that arrival far too often: this step would silently skip binding
  // downloaded_file*, and a later upload step would fail with a "no file path" error that looked
  // like a missing input rather than a download that just hadn't started yet. Wait for an entry
  // to arrive, bounded by the same budget used below for the download itself to finish.
  if (!queue.length) {
    await pollPositive(() => queue.length > 0, DOWNLOAD_WAIT_TIMEOUT_MS);
  }
  if (!queue.length) {
    noDownload(`none arrived within ${DOWNLOAD_WAIT_TIMEOUT_MS}ms — the click before this step may not have triggered one`);
  }
  const pending = queue.shift();
  const entry = await Promise.race([
    pending,
    new Promise(resolve => setTimeout(resolve, DOWNLOAD_WAIT_TIMEOUT_MS)),
  ]);
  // entry is null when server.js's save itself failed, or undefined when the race above timed
  // out before `pending` resolved either way — both mean no real file exists to bind.
  if (!entry || !entry.path) {
    noDownload("it did not finish saving in time");
  }
  // Bind the saved path into `inputs` so a later `upload` step in this same run can reference
  // it — `downloaded_file` always holds the latest download, `downloaded_file_N` (1-indexed,
  // in download order) disambiguates when several downloads happen in one run. See
  // conxa_compile/compiler/upload_binding.py's _BindingState for how the compiler decides which
  // one an upload step's value points at (EXEC-10/W-2 — previously a compiled skill had no way
  // to hand a file from one tab to another without an LLM round-trip per file).
  inputs.downloaded_file = entry.path;
  const n = (inputs.__downloadCount = (inputs.__downloadCount || 0) + 1);
  inputs[`downloaded_file_${n}`] = entry.path;
  // A zip is always extracted at download time (server.js) — bind its sibling extraction
  // folder too, so an upload step the compiler matched against specific files inside that
  // zip (upload_binding.py's _BindingState) has somewhere to resolve `{{downloaded_file_dir}}`
  // / `{{downloaded_file_N_dir}}` against. Absent entirely for a non-zip download.
  if (entry.extractedDir) {
    inputs.downloaded_file_dir = entry.extractedDir;
    inputs[`downloaded_file_${n}_dir`] = entry.extractedDir;
  }
};

// Recorded as { type: "alert"|"confirm"|"prompt", message, value } — the recorder holds the
// native dialog open instead of auto-accepting it, asks the Studio to show it to the human in
// a modal, and records their real choice (accept/dismiss) and, for a prompt, their typed answer
// as `value` (see session.py::_on_dialog + _drain_js_dialog_sync). server.js's
// `page.on("dialog", ...)` listener is what feeds ctx.dialogQueue at replay time; without it
// this queue never fills and these steps silently no-op below.
async function _drainDialogQueue(ctx) {
  const queue = ctx && ctx.dialogQueue;
  if (!queue) return null;
  if (!queue.length) {
    await pollPositive(() => queue.length > 0, DIALOG_WAIT_TIMEOUT_MS);
  }
  return queue.length ? queue.shift() : null;
}

// Shared by the dialog_accept/dialog_dismiss handlers below AND run.js's pre-arm (EXEC-29) —
// both ultimately answer a live Playwright Dialog the same way from the same recorded step
// shape, just at different moments relative to the triggering click. Safe to call twice on the
// same dialog (a pre-armed answer racing the marker step's own drain): Playwright throws on the
// second accept()/dismiss() call, caught and ignored here exactly as it always was.
async function answerDialog(dialog, step, inputs) {
  if (!dialog) return;
  if (step && step.type === "dialog_dismiss") {
    try { await dialog.dismiss(); } catch (_e) { /* dialog already resolved/gone */ }
    return;
  }
  let text = "";
  try {
    const parsed = JSON.parse((step && step.value) || "{}");
    if (parsed && typeof parsed.value === "string") text = parsed.value;
  } catch (_e) { /* non-JSON value: nothing to type */ }
  try {
    await dialog.accept(interpolate(text, inputs));
  } catch (_e) { /* dialog already resolved/gone */ }
}

HANDLERS["dialog_accept"] = async (_page, step, inputs, ctx) => {
  const dialog = await _drainDialogQueue(ctx);
  await answerDialog(dialog, step, inputs);
};

HANDLERS["dialog_dismiss"] = async (_page, step, inputs, ctx) => {
  const dialog = await _drainDialogQueue(ctx);
  await answerDialog(dialog, step, inputs);
};

// Step types run.js's executeOneStep intercepts BEFORE ever reaching this dispatcher: ai_review
// and handover are planned pauses with no selector or handler at all, for_each is a loop
// primitive whose body steps re-enter executeOneStep directly. In normal operation `step.type`
// is never one of these by the time executeStep runs — this set only matters if something calls
// executeStep directly, bypassing run.js (a test harness, say), where the correct behavior is
// still "do nothing" rather than the throw below.
const PRE_DISPATCH_STEP_TYPES = new Set(["ai_review", "handover", "for_each", "outcome_fork"]);

async function executeStep(page, step, inputs, ctx = {}) {
  const handler = HANDLERS[step.type];
  if (handler) { await handler(page, step, inputs, ctx); return; }
  if (PRE_DISPATCH_STEP_TYPES.has(step.type)) return;
  // An unrecognized step type used to be silently skipped and reported as a successful step —
  // harmless for a step that's genuinely decorative, but a safety gate (e.g. `handover`)
  // replayed by a runtime older than the pack that authored it would vanish the same way,
  // letting whatever came after it (often the destructive step the gate exists to guard) run
  // unguarded. Fail loud instead.
  throw Object.assign(
    new Error(`Unrecognized step type "${step.type}" — this runtime does not know how to execute it. Update the Conxa runtime.`),
    { unrecognizedStepType: step.type }
  );
}

// Recovery embedding

function enrichStepsWithRecovery(steps, recovery) {
  if (!Array.isArray(steps)) return steps;

  const recSteps = asArray(recovery && recovery.steps);
  return steps.map((step, idx) => {
    const rec = recSteps.find(item => Number(item && item.step_id) === idx + 1);
    if (!rec) return step;

    const selectorContext = asObject(rec.selector_context);
    // `fallback.text_variants` and `selector_context.alternatives` are no longer emitted by the
    // builder (2026-09) and the `fallback_selectors` this used to synthesize from them had no
    // reader left once the fallback-selector walk was deleted from the cascade. `alternatives` is
    // still spread here rather than dropped, because a pack built before that change still
    // carries it and `candidates` IS live — `try_dismiss` steps read it (handlers.js above).
    const recCandidates = unique([
      selectorContext.primary,
      ...asArray(selectorContext.alternatives),
    ]);

    return {
      ...step,
      candidates: unique([...asArray(step.candidates), ...recCandidates]),
      anchors: asArray(rec.anchors).filter(anchor => anchor && typeof anchor.text === "string" && anchor.text.trim()),
      _intent: rec.intent || "",
      _visual_ref: rec.visual_ref || "",
      // Where this element sat on the page at recording time. The agent tier gets a ranked list
      // of what is on the page NOW; this is the only thing in the payload that says what changed.
      _recorded_context: asObject(rec.recorded_context),
      // Prose description written by the vision LLM at compile time (looked at 5 time-offset
      // frames around the action) — a plain-English caption alongside the short anchor phrases.
      _anchor_sentence: rec.anchor_sentence || "",
      // BUILD-25 stage e: which part of the journey this step belongs to
      // (login/navigate/act/verify/cleanup), written by the compiler's
      // second-opinion pass. "" on any step the pass never ran on or didn't
      // label. Read by failure_response.js's phaseHintBlock.
      _phase: rec.phase || "",
    };
  });
}

// Agent-recovery overrides (Tier 3/4 closing edge)
//
// When the in-process cascade (T1/T2) is exhausted the runtime hands a structured recovery
// request to the MCP agent, which identifies the correct element and resumes with a corrected
// selector for the failing step. `step_overrides` is a map keyed by the 0-based step index
// (the same value passed as `resume_from`) → { candidate_index } or { selector }. We inject
// the chosen selector via the existing `_explicit_selector` channel so it flows through the
// normal string-mode path in withLocator — frame_chain, gating, and pacing are all preserved.
// This is the closing edge of the four-tier cascade: without it, T3/T4 can describe the fix
// but never apply it.
//
// `candidate_index` (Tier 3 reflection contract — browser-use-style nomination): the agent
// picks an entry from the ranked digest; `opts.resolveCandidateIndex(stepIdx, candIdx)` maps
// it to the derived selector captured when that digest was built. The mapping is looked up,
// never trusted blindly — the derived selector still goes through validateOverrideSelector's
// uniqueness-margin gate on resume, exactly like an explicit one.
//
// EXEC-30: `dismiss` is a SEPARATE field from the target override above — it names a control
// to click BEFORE the step's own action runs, for an unrecorded overlay blocking the target
// (never the target itself). Composable with a target override in the same entry: "clear this
// modal, THEN retarget the step". `{ escape: true }` is the plain-Escape form (no selector to
// gate — run.js's dispatch treats it identically to the Tier A ladder's own Escape press).
// The derived selector is NOT trusted here either: run.js routes it through
// dismiss_patterns.js's isSafeDismissLabel gate at the moment it's clicked.
function applyStepOverrides(steps, overrides, opts = {}) {
  if (!Array.isArray(steps) || !overrides || typeof overrides !== "object") return steps;
  const out = steps.slice();
  for (const [rawKey, rawVal] of Object.entries(overrides)) {
    const idx = Number(rawKey);
    if (!Number.isInteger(idx) || idx < 0 || idx >= out.length) continue;

    let selector = null;
    let patch = null;
    if (typeof rawVal === "string") {
      selector = rawVal;
    } else if (rawVal && typeof rawVal === "object") {
      if (typeof rawVal.selector === "string" && rawVal.selector.trim()) {
        selector = rawVal.selector;
      } else if (Number.isInteger(rawVal.candidate_index) && typeof opts.resolveCandidateIndex === "function") {
        selector = opts.resolveCandidateIndex(idx, rawVal.candidate_index);
      }

      const dismiss = rawVal.dismiss;
      if (dismiss && typeof dismiss === "object") {
        let dismissSelector = null;
        if (typeof dismiss.selector === "string" && dismiss.selector.trim()) {
          dismissSelector = dismiss.selector.trim();
        } else if (Number.isInteger(dismiss.candidate_index) && typeof opts.resolveCandidateIndex === "function") {
          dismissSelector = opts.resolveCandidateIndex(idx, dismiss.candidate_index);
        }
        if (dismissSelector) {
          patch = { ...(patch || {}), _dismiss_selector: dismissSelector };
        } else if (dismiss.escape === true) {
          patch = { ...(patch || {}), _dismiss_escape: true };
        }
      }
    }

    if (typeof selector === "string" && selector.trim()) {
      patch = { ...(patch || {}), _explicit_selector: selector.trim() };
    }
    if (!patch) continue;
    // A dismiss-only override still needs the parked failed page adopted on resume (the
    // overlay it targets only exists there) — server.js's _resumeOverride check reads this
    // same flag, so it must be set whether or not a target selector was also given.
    out[idx] = { ...out[idx], ...patch, _agent_override: true };
  }
  return out;
}

module.exports = {
  NOOP_STEP_TYPES,
  probePresent,
  resolvableBranchStep,
  runBranchBody,
  HANDLERS,
  executeStep,
  enrichStepsWithRecovery,
  applyStepOverrides,
  answerDialog,
  _dateValueMatches,
  _ensureChoiceMenuOpen,
};
