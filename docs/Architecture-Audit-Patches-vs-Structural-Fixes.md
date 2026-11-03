# Architecture audit: patches vs. structural fixes

Date: 2026-11-03. Read-only audit; no code was changed.

**Coverage.** 728 commits exist. I read the recent ones (about Aug–Nov 2026) and grepped the code for site and library names. I did not read every commit, so the findings below are what I found, not a complete list.

**A caveat on the GitHub–Drive case.** The headline "Drive fix" is less hardcoded than it looks. The code is generic, and only the docstrings and the self-test name Drive. The weakness is the heuristic underneath. Many other fixes in the repo are tuned to a single site or library.

---

## A. Patches and workarounds

### 1. Recorder timings tuned to HubSpot

- **Status (2026-11-04):** Done. `bridge.js` announces itself over the binding; the pump skips announced frames, rechecks every 15s, backs off failures (0.5s to 4s), prunes on detach, and logs the slowest checks to `recorder_diag.json`. Not yet checked on a real HubSpot-style page (BUILD-61).

- **Where:** `conxa-builder/python/conxa_compile/recorder/session.py:53-72` (`_NO_PAGES_GRACE_TICKS`, raised from 8s to 20s) and `:1461-1489` (`_BRIDGE_INSTALL_SETTLE_S`, `_BRIDGE_INSTALL_MAX_ATTEMPTS`).
- **History:** `caf2352` ("recorder frame-recovery hardening") and nearby commits. I could not tell which commit set each constant.
- **What changed and why:** HubSpot's "Create new" panel keeps re-creating its iframe for seconds. The recorder went silent or ended early. The comments say the settle delay was raised from 0.3s to 2.5s, and the no-pages grace from 8s to 20s, "measured against real HubSpot recordings".
- **Why it is a patch:** Every number was found by tuning against one vendor. Another app with a heavier iframe will need another bump.
- **What remains:** The recorder runs one pump thread that makes Playwright calls with no timeout. A single stuck call can freeze the whole recording. The comments admit this ("no safe cross-thread watchdog is possible").
- **General fix:** Probe fewer frames, not on another thread. Sync Playwright is bound to the thread that created it (`session.py:1870-1876`), so the calls cannot move to a worker. Instead the page announces "bridge installed" over the existing exposed binding, and the pump probes only frames that have not announced, with backoff. Then "settle time" stops being a per-app tuning knob. (Correction: an earlier version of this audit proposed a worker thread per frame; that is not possible.)

### 2. Same-site redirect fold (`drive.google.com` → `?pli=1` → `/drive/home`)

- **Status (2026-11-04):** Partly done. Tests added for the existing fold. The recorder tag was not added: no recorded session was available, and page-requested navigations are already skipped before a `manual_navigate` is emitted, so the CDP reason cannot separate a redirect from a typed address without one. See BUILD-61.

- **Where:** `compiler/navigation_effects.py:88-99`. Commit `c0234b5` (2026-11-03). Sibling patches: `6199e05` (fold action-caused navigations) and `36bd0a5` (collapse a duplicated goto after a new tab).
- **What it does:** It drops a navigation if the previous navigation on that tab landed on its `from_url`, the host is the same, and it happened within 5 seconds (`_LEGACY_WINDOW_S`).
- **Why it is a patch:** It guesses "the site redirected" from timing. The recorder already knows where each navigation came from, and the browser can say it outright.
- **Real failure:** A person who loads a page and, within 5s, deliberately navigates to another path on the same host is read as a redirect. Their step is silently dropped.
- **Inconsistent layering:** The same problem, extra navigation steps, is now fixed in three places: the recorder's `cause` tag, the compile-time fold, and the LLM `flag_noise` pass (`36bd0a5`), which removes a duplicate goto when the URL and tab match the previous step.
- **Misuse of a fallback:** `_LEGACY_WINDOW_S` is the fallback for old recordings without cause tags. The new redirect rule reuses it for current recordings.
- **General fix:** Have the recorder stamp each navigation with how it happened. Chrome exposes this through the request's `redirectedFrom` chain and CDP `Network.requestWillBeSent.redirectResponse`; page-script (client-side) redirects can be told apart by their initiator. The compiler then drops by tag, with no clock and no LLM.

### 3. Keyboard-shortcut stripping for Drive ("File upload Alt+C then U")

- **Status (2026-11-04):** Done. One public `strip_keyboard_shortcut_tail`; the clarify copy now uses it; `runtime/test/fixtures/accessible_name_cases.json` is run by Python and node.

- **Where:** `compiler/identity_bundle.py:42-57`. Commit `1f6a265` (2026-09-07). A second copy lives in `runtime/app/cascade.js:178-184` (`a11yRecoveryName`), and a looser third copy in `conversation/clarify.py:83` (`_SHORTCUT_HINT`). (Correction: an earlier version of this audit named `resolve_adapter.js`; that file only has a comment about it.)
- **Why it is a patch:** The comment names Google Drive. The regex exists because one site appends accelerator hints to control names. The same cleanup is now written three times, and the clarify copy is greedy, so "Use Ctrl+Click to select" becomes "Use".
- **General fix:** Match accessible names by prefix or token containment, the way Playwright's non-exact matching does. Keep one name-normalization function shared by compiler and runtime, backed by a shared fixture file. The repo already does this for login detection and decision popups. The regex is not wrong, just duplicated and site-motivated.

### 4. "Ignore everything on a login page" (recorder)

- **Status (2026-11-04):** Done. `LoginFlowTracker` (strict paths; SSO hops only mid sign-in), a shared fixture, and a `login_page` ledger entry written to `login_drops.json` (no values).

- **Where:** `recorder/frame_utils.py:213-220` and the filter at `recorder/session.py:1817`. Commit `a3114c5` (2026-11-02).
- **What it does:** It drops every event whose URL path matches `/(login|signin|auth|oauth|sso|logout|…)/`.
- **Why it is a patch:** It fixes one case, an expired session mid-recording, by guessing from the URL.
- **Real failure:** A legitimate app page under `/auth/...` or `/sso/...` loses all its recorded actions, with no compile-ledger entry.
- **Three divergent copies:**
  - Python: `frame_utils._LOGIN_PATH_RE` (includes `auth|oauth|sso|logout`).
  - Runtime: `login_signals.LOGIN_PATH_RE` (only `login|signin|sign-in|session-expired`).
  - Runtime: `browser.js:48` `LOGIN_URL_PATTERNS` (a third list).
- **General fix:** The runtime already solved this correctly (see section C). Ask "is this tab signed out?" using the saved signed-out baseline, not path words. Anything dropped should be recorded in the compile ledger the way `not_from_user` events are.

### 5. Date-picker detection by library class names

- **Status (2026-11-04):** Deferred (BUILD-61). Outcome-based collapse needs recorder data that does not exist yet.

- **Where:** `recorder/bridge.js:926-932` (`CALENDAR_ROOT_SELECTORS`), plus `selector_filters.py:116-127` and `identity_bundle.py:192` (react-datepicker special cases). Commit `e66d0be` (2026-08-30) and the later `74c362a`.
- **What it does:** It tags clicks as a "date pick" by matching `.flatpickr-calendar`, `.react-datepicker`, `[class*='MuiDateCalendar']`, `[class*='calendar']`, and similar.
- **Why it is a patch:** Class names are version-fragile, and `[class*='calendar']` also matches calendar apps. The code fails closed (it records the click normally), which is good. The cost is that unknown pickers silently get no help.
- **General fix:** Detect by outcome. Any run of clicks that ends with a date-shaped value in an input should collapse to one `date_pick`; use ARIA structure (`role=grid` / `gridcell`) only as a hint. This needs recorder data that does not exist yet: a day-cell click is recorded as a plain `click` with no `value_set` effect and no before-value (`bridge.js:1681`). (Correction: an earlier version said `convert_typed_date_steps` already works by outcome. It is in `date_picker.py:312` and works from the typed text, placeholder and a locale sample, not from before/after values.) Deferred to TODO.md.

### 6. Ephemeral-anchor keyword list

- **Status (2026-11-04):** Partly done. Whole-word matching; bare `notification` and `subscribe` removed; `toast` and `snackbar` added. Structural detection is deferred (BUILD-61).

- **Where:** `compiler/selector_filters.py:455-480`. Commit `6edb387` (2026-06-27).
- **What it does:** It rejects any anchor phrase containing "cookie", "banner", "popup", "notification", "subscribe", and similar.
- **Why it is a patch:** This is an English-only substring list. A real label such as "Notification settings" or "Subscribe to updates" is rejected as an anchor.
- **General fix:** Judge by structure and behavior: the element sits in a `role=dialog`, `alert`, or `status` region, is fixed-position, or is absent in other recordings of the same workflow.

### 7. One global switch, `compile_keep_every_action`

- **Status (2026-11-04):** Done. `settings.compile_pass(name)` with per-pass overrides; outcome forks always on. Id-keyed passes are deferred (BUILD-61).

- **Where:** `packages/conxa-core/conxa_core/config.py:187`, with 12 branch points. Examples: `build.py:1614, 1699, 1713, 1736`; `variant_align.py:145`; `pipeline/run.py:191`; `clarify.py:875`; `timeline_understanding.py:132`.
- **History:** Added as default true in `6053184` (2026-11-02). Flipped to false in `c0234b5` (2026-11-03), one day later. The tests patch it both ways.
- **What it does:** It turns off all the cleanup passes together: choice collapse, date collapse, outcome forks, dedupe, variant alignment.
- **Why it is a patch:** It is a kill-switch for quality problems in several separate passes. Fixing one pass requires flipping the whole system.
- **Safety gap:** With the flag true, `build_outcome_forks` is skipped (`build.py:1736`). That is the pass the "a popup that asks for a decision is never answered on anyone's behalf" invariant depends on. The default is false today, so nothing is broken, but the safety rule depends on a flag that was flipped yesterday.
- **Deeper cause:** `build.py` is 2,255 lines and runs passes in a fixed order. The comments say things like "must run here while `steps[i]` still maps 1:1 onto `cleaned_events[i]`". The passes depend on list positions, so each new pass has to be slotted in carefully.
- **General fix:** Give each pass its own name and enable switch, and give each step a stable `source_event` id (one already exists). Passes should key off ids, not list position. Then each pass can be tested, enabled, or disabled alone.

### Smaller items

- **LLM prompt band-aids (uncommitted edits).** `packages/conxa-core/conxa_core/llm/client.py` now tells the model "Never ask about a gap, idle time, a download bar…", and `max_tokens` went from 6144 to 16384 (the code comment calls this a "HYPOTHESIS", which is honest). The prompt rule should become code: drop any question whose referenced item is a gap, the way code already strips unsafe guesses.
- **Dependency on hand-synced twins.** About 31 places say "twin", "mirror", or "keep in sync by hand". Examples: `OPTION_CONTENT_TAGS` in three files, `runtime/app/page_scripts.js:544-552` (PROBE_SCRIPT), and `url_matches_pattern`. Some twins have a shared fixture file; the rest only have a comment.

---

## B. Underlying architectural problems

1. **The recorder under-describes what happened.** It records "a navigation happened" but not why. The compiler then reconstructs the cause from timestamps and URL shapes. This is behind finding 2 and the earlier fixes `6199e05` and `36bd0a5`.
2. **Heuristics keyed to one app's markup.** Findings 1, 3, 5, and 6 each fix one app or widget library with a list.
3. **Cleanup passes are positional and flag-controlled.** Finding 7.
4. **Duplicated logic across Python and JS.** Login rules, name cleanup, option tags.
5. **God files.** `session.py` 3,216 lines, `bridge.js` 2,749, `server.js` 2,621, `build.py` 2,255, `browser.js` 1,792. Every patch lands in these, which makes patches hard to review and hard to remove.

---

## C. Already sound, do not rebuild

- **Login/sign-in detection in the runtime.** `a255761` (2026-09-25) replaced a hardcoded list of identity-provider hosts and a `success_url` check with a signed-out baseline comparison (`runtime/app/login_signals.js`, `browser.js:108-118`). The comments name the earlier special-casing as a mistake, and the change came with shared fixtures (`auth_definition_cases.json`). This is the model for the other fixes.
- **Script-vs-human event filter** (`c0234b5`, `bridge.js:67-76`). It uses `isTrusted`, which the browser decides. It has no site names and runs in one place.
- **IdentityBundle plus `selector_grammar`, and the resolver margin gate.** Deterministic and unit-testable.
- **Outcome forks** with the shared `decision_surface_cases.json` fixture across runtime and compiler.
- **The app map's scrub step** (`app_graph/scrub.py`) and the rule that LLM output is validated in code.
- **`storage_state.keeps_session`** (`a3114c5`). It protects saved sign-ins with a rule that has nothing app-specific in it.
- **Typed-date detection** (`date_format.py::detect_date_format`, called from `date_picker.py::convert_typed_date_steps`). It works from the typed text and a locale sample, not from widget names. It is not outcome-based (see finding 5).
- **Per-step tab resolution and the iframe chain.** Both already fail closed.

---

## Priority order if acting on this

1. Recorder emits navigation provenance (fixes finding 2, and lets you delete `_LEGACY_WINDOW_S` and one `flag_noise` branch).
2. Probe recorder frames only until the page reports its bridge is installed (finding 1).
3. Drop the login-path filter in the recorder; use the baseline probe, and ledger anything dropped (finding 4).
4. Per-pass switches in place of `compile_keep_every_action` (finding 7).
5. Shared fixtures for every Python/JS twin (the pattern already exists).

## Not checked

- No tests were run and no recording was replayed.
- The `timeline_understanding.py` uncommitted diff was not read beyond its stat.
- Which commit set each HubSpot constant was not isolated.
