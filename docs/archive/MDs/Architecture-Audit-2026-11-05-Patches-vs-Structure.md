# Architecture audit: where we patched instead of solving the problem

Date: 2026-11-05. Read-only audit. No code was changed and no tests were run.

This is the second pass. The first pass, `docs/Architecture-Audit-Patches-vs-Structural-Fixes.md` (2026-11-03), found seven patches. Most were fixed on 2026-11-04 (commits `cf63fcb` and `7716558`). This pass does three things:

1. It checks whether those fixes really fixed the cause or only moved it.
2. It looks past the GitHub–Drive recording for patches in other parts of the system.
3. It lists the parts that are sound, so we do not rebuild them.

## How much I read, and what I could not tell

- The repo has 731 commits. Since 2026-07-01 there are 596 commits, and 171 of them (29%) start with `fix`. That is a lot of fixing.
- I read the commit messages for the themes below (date pickers, sign-in, recorder frames, navigation). I read the code behind every finding and checked it against the current files.
- I did not read every commit or every file. Cloud billing, the Execute app and the frontend got only a light scan.
- **Dates are approximate for some constants.** Several were introduced inside bulk commits, for example `c17ba39` "chore: commit pending studio, runtime, cloud and docs changes", `b993154` "Reftore the runtime", `0ea9f5a` "Add daily fix logs", and `f038410` "Add tests for authentication recovery…". The reason for the change is not recoverable from those commits. This is a finding in itself (see section B8).

---

## Summary

| # | Finding | Kind | Risk |
|---|---|---|---|
| A1 | The new navigation `cause` tag is still a 5-second clock, now inside the recorder | Fix that moved the guess | Medium. A deliberate navigation can be dropped |
| A2 | "Is this a sign-in page?" is written about nine times, and only two copies share a test file | Fix that left copies behind | Medium. Copies disagree |
| A3 | Date pickers are still found by library class names; the new outcome check only removes false alarms | Half a fix | Low. It fails closed |
| B1 | "Destructive" is decided by six English words | Patch | **High.** A missed word means a risky click is not flagged |
| B2 | Cloud LLM router has a model-name special case, and the token limit is a hand-tuned table | Patch | Medium |
| B3 | "Same site" is defined four different ways | Duplicate logic | Medium |
| B4 | Compile passes still pair `steps[i]` with `events[i]` by list position | Known debt | Medium |
| B5 | Element-name rules were learned from one demo site and use magic numbers; resolver and compiler disagree | Patch | Low–Medium |
| B6 | Recorder iframe timing constants were tuned against HubSpot and are still there | Patch | Medium |
| B7 | Temporary fallbacks have no removal trigger | Debt | Low |
| B8 | Bulk commits hide the reason for tuned numbers | Process | Medium |
| B9 | God files | Structure | Medium |

---

## A. The previous fixes: what held and what did not

### A1. The navigation `cause` tag is still a 5-second clock

- **What was changed and why:** In `7716558` the recorder started tagging every `manual_navigate` as `action`, `redirect` or `user`. The goal was to stop the compiler from guessing "the site redirected" by timing. The compiler now trusts the tag (`compiler/navigation_effects.py`, `_is_action_caused` and `_is_site_redirect`).
- **Where the guess went:** The tag is made in `recorder/session.py:1405-1430`. It uses a 5-second constant, `_NAV_INTERACTION_WINDOW_S = 5.0` (`session.py:644`).
  - "Within 5 seconds after a click or keypress" becomes `action`.
  - "Same host as the last navigation and within 5 seconds" becomes `redirect`.
  - Anything else becomes `user`.
  The constant first appeared in `6199e05` (2026-10-01) as a recorder rule. `7716558` reused it for the `redirect` call.
- **Why it is still a workaround:** The browser does know the real answer. The code reads only part of it. `TODO.md` BUILD-61 admits that the CDP `reason` of `Page.frameRequestedNavigation` is ignored. A person who clicks a button and then pastes a URL into the address bar within 5 seconds is tagged `action`, and the compiler folds that step away.
- **Smaller problems in the same code:**
  - `same_document` navigations are always tagged `action`. The code comment says a user typing a hash-only URL lands here too ("rare, accepted").
  - `_same_site` (`session.py:394`) compares the whole host, so `app.example.com` and `www.example.com` are different sites.
- **What remains:** The compiler no longer guesses, but the recorder still does. It is the same bug in a different file.
- **A reusable solution:** Record the browser's own facts and drop the clock. Candidates to test (I have not tried them):
  - Playwright's `request.redirected_from` chain for server redirects.
  - The CDP navigation `reason` for browser-made versus page-made navigations.
  - The Navigation API `navigate` event, which carries `userInitiated` and `navigationType`, for same-page route changes.
  Use the clock only for sessions recorded before the tag existed.

### A2. "Is this a sign-in page?" now exists about nine times

The 2026-11-04 fix made the recorder's drop stricter (`LoginFlowTracker`). It was a real improvement. But the same question is still answered separately in each of these places:

| Copy | Where |
|---|---|
| `_LOGIN_URL_PATTERNS`, a substring list including `"auth"` (so `/authors` matches) | `recorder/frame_utils.py:213` |
| `_LOGIN_PATH_RE` (loose), used by `looks_like_login_page` in the recording gate | `frame_utils.py:216`, callers in `handlers/session.py:94` and `handlers/workflow_flow.py:220` |
| `_STRICT_LOGIN_PATH_RE` and `_SSO_HOP_PATH_RE` | `frame_utils.py:228-229` |
| `_SESSION_LOGIN_PATH_RE` | `conxa_compile/auth_learning.py:143` |
| `LOGIN_PATH_RE` (narrow) | `runtime/app/login_signals.js:88` |
| `SESSION_LOGIN_PATH_RE` (wide) | `login_signals.js:341` |
| `LOGIN_URL_PATTERNS` (substring list) | `runtime/app/browser.js:48` |
| `AUTH_FAILURE_URL_RE` (includes `auth` and `logout`) and `AUTH_FAILURE_TITLE_RE` (English words) | `runtime/app/run.js:1410-1411` |

- **Why this is a problem:** The first audit called out exactly this. The recorder fix removed the "legit `/auth/` page loses its steps" bug in one place. The runtime's `AUTH_FAILURE_URL_RE` still treats any `/auth/` path as "your session expired". `run.js` then adds a band-aid, `_isRecordedUrl`, so a login page the workflow deliberately visits is not treated as an expiry. The comments say "don't merge them". That is a decision to keep the duplicates, not a design.
- **What the shared fixture covers:** `runtime/test/fixtures/login_path_cases.json` is read by two tests only. The `recorder_drop` and `session_login` columns are checked. `LOGIN_URL_PATTERNS`, `AUTH_FAILURE_URL_RE`, `_LOGIN_URL_PATTERNS` and the loose `_LOGIN_PATH_RE` have no fixture.
- **Possibly dead code:** `classify_login_flow` (`frame_utils.py:259`) has no caller outside its own definition in the files I searched. It carries the substring list that matches `author`.
- **A reusable solution:**
  1. Keep the runtime's signed-out baseline compare as the primary test (it is sound, see section C).
  2. Put the path words in one data file read by both Python and JavaScript. Name each use: `recorder_drop`, `session_check`, `auth_failure`.
  3. Run every use against `login_path_cases.json`.
  4. Delete `classify_login_flow` if nothing calls it.

### A3. Date pickers: found by class name; the new check removes false alarms only

- **What was changed:** `7716558` records the date field's value before and after a day click (`field_display_value` and `field_display_value_after`). `compiler/date_picker.py::_left_field_unchanged` then refuses to call a click a "pick" when the field did not change. This fixes false positives such as a calendar app's cell.
- **What did not change:** A click is still only considered a date pick if it lands inside a calendar the recorder recognises by class name. The list is `CALENDAR_ROOT_SELECTORS` in `recorder/bridge.js:956-961`: `.flatpickr-calendar`, `.react-datepicker`, `[class*='MuiDateCalendar']`, `.ui-datepicker`, `[class*='calendar']` and others. It was added in `e66d0be` (2026-08-30). `TODO.md` BUILD-61 (3) says so.
- **Why it matters:** An unknown picker is still invisible. The new check can only cancel a pick, never find one.
- **History:** Date pickers have been touched by at least 14 commits since August: `187fc13`, `e81e288`, `7cb718a`, `32fbabf`, `02fb5ae`, `1246c50`, `b1d7600`, `ee83fec`, `74c362a`, `7716558` and others. Many were fixes for one library each (react-datepicker, jQuery UI, MUI).
- **Also English-only:** `runtime/app/date_picker.js` parses month names in English. The file says so openly.
- **A reusable solution:** Detect by what happened. Any group of clicks after which a date-shaped value appears in an input collapses to one `date_pick`. Use ARIA structure (`role=grid` or `gridcell`, a dialog near the input) as a hint, and keep the class list only as a fallback for old recordings. The before/after field value that `7716558` added is the missing input for this.
- **Good news:** This path fails closed. An unrecognised picker is recorded as ordinary clicks, not broken.

---

## B. Patches found beyond the first audit

### B1. "Destructive" is decided by six English words (highest risk)

- **Where:** `confidence/uncertainty.py:9`: `DESTRUCTIVE_TOKENS = ("delete", "remove", "revoke", "destroy", "purge", "cancel_subscription")`. Introduced in `f038410`, a tests commit, so there is no recorded reason. It is used by `compiler/destructive_semantics.py::step_has_destructive_intent`, which feeds the "irreversible" label (`classify_consequence`) and the record-scoping safeguards.
- **What it does:** It checks whether an LLM-written intent sentence contains one of these words. A policy file can add more words (`destructive_intent_substrings`). The step can also be marked explicitly (`semantic.is_destructive`).
- **Why it is a patch:** The check is a vocabulary test. "Archive", "Terminate", "Unsubscribe", "Disconnect", "Deactivate", "Wipe" and any non-English label do not match. "Remove filter" does. Because it is the safety gate for irreversible clicks, a miss fails open.
- **Better example next to it:** `compiler/outcome_fork.py` also uses English lists (`_CONSEQUENTIAL_RE`, `_PURE_DISMISS`, `_CONSENT_RE`). That list is narrowly scoped, pinned by the shared `decision_surface_cases.json` fixture, and fails closed. The destructive list has none of those protections.
- **A reusable solution:**
  - Make "irreversible" an explicit, reviewed field. The compiler already records the click's real effect (`post_condition.classified_effect`). Where the click ends in a confirmation dialog or removes the record from a list, that is a stronger signal than a word.
  - Keep a word list only as a hint. Move it to data, add a fixture, and ask a person when the step cannot be classified.

### B2. LLM router and token limits: model-name special cases

- **Where:**
  - `conxa-cloud/backend/app/llm/router.py:578`: `if "openrouter.ai" in entry.endpoint and "kimi-k3" in model` adds `reasoning: {"enabled": True}`. It came in with `c17ba39`, a bulk commit.
  - `packages/conxa-core/conxa_core/llm/client.py` (~line 980-1005): a chain of `if task == ...: body["max_tokens"] = ...`. The understanding call went from 6144 to 16384 with a comment that reads "HYPOTHESIS (2026-11-02)".
- **Why it is a patch:** The first is a quirk of one model on one provider, written as a string match inside generic routing code. The second is a guess that a bigger budget stops truncated replies.
- **What remains:** There is no per-model capability table. A reply that is cut off is not detected as cut off, so every task keeps a large fixed budget.
- **A reusable solution:** Put provider and model traits (reasoning on/off, output cap, JSON mode) in the router's config, one row per model. Retry once with a larger budget when `finish_reason` is `length`. The router already has a `reasoning_only_no_content` guard (`router.py:659-673`), so the pattern is partly there.

### B3. "Same site" is defined four ways

| Where | What it means |
|---|---|
| `recorder/session.py:394` `_same_site` | Same hostname, `www.` removed |
| `compiler/navigation_effects.py:_host` | The same, written again |
| `packages/conxa-core/.../group_store.py:_site` | Last two labels of the host. The code admits `a.co.uk` and `b.co.uk` read as the same site |
| `runtime/app/browser.js` (~line 156) | Cookie domain matching |

- **Why it matters:** Sign-in matching and redirect folding both depend on "same site". If they disagree, a workflow can be called covered by one part and uncovered by another. `b83a5d3` ("match saved sign-in by site, not exact hostname") fixed one case.
- **A reusable solution:** One helper backed by the public-suffix list, in `conxa-core` for Python and in the runtime for JavaScript, with one shared fixture of host pairs.

### B4. Compile passes still pair steps with events by list position

- **Where:** `compiler/build.py` (the `compile_skill_package` section, ~lines 1700-1760). `collapse_choice_group_runs`, `collapse_date_picker_runs`, `convert_typed_date_steps`, `_populate_hover_chains` and the upload binding read `steps[i]` next to `cleaned_events[i]`. The comments say "must run here while `steps[i]` still maps 1:1…". `date_picker.py:381` raises `ValueError` if the lengths differ.
- **Why it is a workaround:** The guard turns a silent mix-up into a crash. It does not remove the dependency on order.
- **Progress:** The first audit's suggested per-pass switches are done (`config.py:188-202`, `compile_pass()`). Each switch is now independent.
- **What remains:** The switches do not make the passes order-proof. `TODO.md` BUILD-61 (2) lists this.
- **A reusable solution:** Match each step to its event by `SkillStep.source_event`, which already exists. Then a pass can add or remove steps without breaking the others, and the order stops mattering.

### B5. Element-name rules learned from one demo site

- **Where:**
  - `compiler/identity_bundle.py:~228`: an inner-text longer than 80 characters is "fabricated", not a name. Mirrored in `runtime/app/cascade.js`.
  - `compiler/selector_filters.py:123-159` `salvage_deep_css_tail`, with a depth limit of 6 and a length cap of 220.
  - `runtime/app/resolver.js:56` and `runtime/test/unit/test_resolver.js:189,215`: the regression tests and comments are about react-datepicker's year `<select>` on demoqa.
- **Why they are patches:** The reasoning is sound and follows how browsers compute accessible names (see `_NAME_FROM_CONTENT_ROLES` in section C). The numbers (80, 6, 220) are tuned to what one library produced.
- **Drift:** `resolver.js::fpName` orders text and placeholder differently from `identity_bundle.py` and `cascade.js` (BUILD-61 item 5). The three are meant to stay in step but are not.
- **A reusable solution:** One name function shared by compiler and runtime, run against `accessible_name_cases.json`, which is the pattern already used for shortcut stripping.

### B6. Recorder frame timing: constants still tuned to HubSpot

- **Where:** `recorder/session.py`
  - `_NO_PAGES_GRACE_TICKS = 20` (line 72), raised from 8 seconds after "real HubSpot recordings". Introduced in `0ea9f5a`.
  - `_BRIDGE_INSTALL_SETTLE_S = 2.5`, `_BRIDGE_INSTALL_MAX_ATTEMPTS = 5` and `_BRIDGE_INSTALL_RETRY_DELAY_S = 0.05` (lines 1507-1526). Introduced in `caf2352`.
  - The regression test `test_bridge_reinjection.py` is named for HubSpot's contact panel.
- **What 2026-11-04 changed:** The page now announces "bridge installed" and the pump probes less (`_BRIDGE_RECHECK_S`, back-off 0.5 to 4 s). That is the right direction.
- **What remains:** The old constants are still gates in front of the new logic. The comments say a stuck Playwright call can freeze the single pump thread. Nobody has run a real HubSpot recording against the new code (BUILD-61 item 7).
- **A reusable solution:** Verify the announce path on a real page of this type, then delete the settle delay if the logs show it is no longer needed. The deeper limit is that sync Playwright cannot time out a call on another thread. Moving the recorder onto the async API or a direct CDP session would remove that limit. I have not checked how large that change is.

### B7. Temporary fallbacks with no removal trigger

- `navigation_effects._LEGACY_WINDOW_S = 5.0` is meant for old recordings only, but nothing detects when no old recordings remain.
- The `flag_noise` duplicate-goto branch (from `36bd0a5`) overlaps the navigation fold.
- The English ephemeral-anchor keyword list (`selector_filters.py:459`) stays as the fallback for old recordings.

Each is reasonable on its own. Together, three layers fix the same symptom (extra navigation steps), as the first audit noted. A removal condition ("delete when X is confirmed on a real recording") should be in the code or in a test, not only in `TODO.md`.

### B8. Commit hygiene hides the reasons

The constants that matter most were added in commits named "chore: commit pending…", "Add daily fix logs…", "Reftore the runtime", "Add tests…". A reviewer cannot tell why `2.5` or `20` was chosen. This is why parts of this audit could not date a constant.

- **Solution:** One reason per commit. A tuned number should cite a session id or fixture in a comment. The 2026-11-04 commits did this well, and the older ones did not.

### B9. God files

Line counts today: `recorder/session.py` 3,325; `recorder/bridge.js` 2,789; `runtime/app/server.js` 2,621; `compiler/build.py` 2,274; `runtime/app/browser.js` 1,792; `entitlements.py` 1,503; `StepConfigForm.tsx` 1,493; `workflow_mutations.py` 1,492; `run.js` 1,473.

The most-patched files match the largest ones. In fix commits since July, `build.py` appears 17 times, `session.py` 15, `server.js` and `handlers.js` 10 each. Every patch lands in these files, which makes patches hard to review and hard to remove.

---

## C. Already sound: do not rebuild

- **Runtime sign-in detection against a signed-out baseline** (`runtime/app/login_signals.js`, `a255761`, with shared fixtures `auth_definition_cases.json` and `session_check_cases.json`). It asks "does this page differ from the signed-out page I saved", not "does the URL contain a word". The path regexes in section A2 are separate; the baseline comparison is the model to follow.
- **The script-versus-person filter** (`recorder/bridge.js:67-76`). The browser's own `isTrusted` flag decides, with no site names.
- **IdentityBundle and `selector_grammar.py`, plus the resolver's margin gate** (`runtime/app/resolver.js:156-199`, `uniqueMargin`). Deterministic and unit-tested. The resolver never picks the first candidate blindly.
- **Outcome forks and `decision_surface_cases.json`.** One fixture decides what counts as a decision for both Python and JavaScript, and the design fails closed.
- **`app_graph/scrub.py`.** All text enters the app map through one scrubber, and code validates every LLM answer.
- **`storage_state.keeps_session`** (`packages/conxa-core/.../storage_state.py:48`). It uses each app's learned session cookie names, so nothing is hard-coded per site.
- **Per-pass compile switches and the compile ledger** (`config.py:188-202`, `compile_report["dropped"]`). Every removal is recorded and each pass is separately switchable.
- **The shared-fixture pattern**: `accessible_name_cases.json` (keyboard-shortcut tail, run by Python and node), `decision_surface_cases.json`, `login_path_cases.json`, `scrub_cases.json`, plus `test_option_content_tags_parity.py`. This is the right way to keep two languages honest. The gap is that it is not applied everywhere (A2, B5).
- **ARIA-derived name rules** (`_NAME_FROM_CONTENT_ROLES` in `identity_bundle.py` and `resolver.js`). They follow the accessible-name specification, not one site.
- **The bounded overlay-dismiss list** (`runtime/app/dismiss_patterns.js`). It names specific consent toolkits, writes its safety rules in the header and only fires after a click is intercepted. It is English-leaning but it is bounded and never answers a decision.
- **Per-step tab resolution and the iframe chain.** `CLAUDE.md` lists them as invariants that fail closed. I did not re-read that code in this pass.

---

## Suggested order

1. **B1 destructive detection.** It is a safety gate and the cheapest to improve: move the words to data, add a fixture, treat "cannot classify" as ask.
2. **A2 one sign-in rule table** with named uses, and delete the dead function if confirmed.
3. **A1 navigation cause from browser facts.** Test the candidates in a throw-away recording first.
4. **B4 `source_event` keys** so the passes stop depending on order.
5. **B3 one site-identity helper.**
6. **A3 outcome-first date picks.**
7. **B2 model capability table.**
8. **B6, B7:** run the manual checks listed in `TODO.md` BUILD-61 (7). Delete a fallback only after a real recording confirms its replacement.
9. **B8** as a team habit, starting now.

## Not checked

- No tests were run and no recording was replayed. Every "still open" claim above comes from reading code and `TODO.md`.
- I did not verify the Navigation API or CDP candidates in A1; they are leads to test.
- Cloud billing and entitlements, the Execute app and the dashboard frontend were scanned, not audited.
- The commit dates in B1, B2 and B6 point at the first commit that contains the text. Several are bulk commits, so the date is when the text first landed, not when the decision was made.
