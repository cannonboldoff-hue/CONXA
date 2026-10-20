# Spike: recording inside Build Studio's own browser panel (Gate A) — 2026-10-20

**Question.** Can the recorder run against an Electron `WebContentsView` inside Build Studio, driven over CDP by Python Playwright 1.59, and still produce what compile needs: bridge events from every frame/tab, a fixed 1280×720 DPR-1 page, per-tab `.webm` video timed to the event clock, and a signed-in state that loads and saves?

**Answer: yes.** All five experiments pass. Plan: `~/.claude/plans/yes-go-ahead-with-mossy-heron.md` (Phase 0). The harness was a scratch Electron 42.3.0 app (fake `file://` Studio window + panel-style views + a loopback control server), a two-origin fixture site, and a Python driver. The harness itself was not committed; the parts that matter are carried into Phases 1–3.

Machine: Windows 11, display scale factor 1.25 (so every "full-resolution" frame is 1600×900 device px).

## Results

| # | Experiment | Result |
|---|---|---|
| E1 | Context-level `expose_binding("__skillReport")` + `add_init_script(bridge, dialog override)` over `connect_over_cdp`, each wrapped in `if (location.protocol !== 'file:') { … }` | **Pass.** The bridge is present before page scripts in the main frame and in both iframes (same- and cross-origin). The `file://` Studio window runs neither script. Only the binding *function* shows up there, and it is harmless because the sink can drop it by page. Clicks/typing via `webContents.sendInputEvent` land, including inside both iframes. `filechooser` + `set_files` and Playwright's `download` event fire. 62 payloads, 0 from Studio. `browser.close()` only disconnects: the views survive and the scripts are gone after the next connect. |
| E2 | Popups via `setWindowOpenHandler → {action:"allow", createWindow}` adopting `options.webContents` into a new `WebContentsView` | **Pass.** Playwright sees the popup, `page.opener()` is the opener page, `window.opener.postMessage` works, and the panel lists it as a second tab with its opener. **`deny` + open-our-own-view crashed Electron (crashpad) — don't use it.** |
| E3 | Fixed 1280×720 DPR-1 page scaled into the panel | **Pass with CDP `Emulation.setDeviceMetricsOverride({width:1280,height:720,deviceScaleFactor:1,mobile:false,scale:s})` issued by Electron through `webContents.debugger`.** `innerWidth`/`innerHeight`/`devicePixelRatio` = 1280/720/1 at panel widths 820, 1216, 1600, and it survives navigation. `sendInputEvent` at `css × s` hits the intended element (s = 0.64 and 0.95). **`webContents.enableDeviceEmulation` was lost on the first navigation — don't use it.** |
| E4 | Our own screencast → ffmpeg video, timed with an encoded clock | **Pass, better than today's recorder** (table below). |
| E5 | Sign-in state into/out of an in-memory run partition (`Network.setCookies` / `getAllCookies` on a run page's CDP session + localStorage per origin) | **Pass.** A fixture login's cookie and localStorage are restored in a fresh partition. `context.cookies()` only sees Electron's *default* session, so it must never be used for run state (AUTH-21 pattern). Nothing from Studio leaked. |

### E4 timing (encoded-clock decode at T−500/−250/0/+250/+500 ms around clicks, via `frame_extractor._extract_frame`)

| Run | Median abs error | p95 | Max | Within ±80 ms | CPU (12 s run) |
|---|---|---|---|---|---|
| Today's recorder (`record_video_dir`) | 52 ms | — | 73 ms | 100% | — |
| Panel 1216 px (s = 0.95) | 31 / 39 around clicks | 45 / 49 | 59 | 100% | 0.5 s |
| Panel 820 px (s = 0.64) | 16 / 22 | 30 / 38 | 43 | 100% | 0.3 s |
| Panel resized 1216 → 820 mid-run | 14 / 21 | 37 / 42 | 49 | 100% | 6.9 s |

Element crops (`visual.crop_element_from_frame` on the bridge's CSS bbox) land on the red target before and after a resize. Screencast delivers about 108 fps while the clock repaints every frame, and far fewer on a static page.

## What we learned (carry into the implementation)

1. **Screencast frames are not page-sized.** With emulation `scale`, every frame is the view's full device-pixel size, `1280·D × 720·D` (D = display scale factor). The page is drawn shrunk by `s` into its top-left corner, and nothing in the frame metadata says so. The recorder must be **told `s` by the panel** (control op `scale {runId, tabId}`, polled every 200 ms). It crops `iw·s × ih·s` and scales back to 1280×720 so bridge bboxes line up with compile's crops.
2. **Do the crop/scale in ffmpeg, not Python.** PIL normalisation cost a full CPU core (20–33 ms per frame at ~100 fps). As an ffmpeg `-vf crop=…,scale=1280:720,pad,crop` it is nearly free.
3. **Playwright's bundled ffmpeg has no concat** (demuxers: image2pipe, matroska/webm only; no concat demuxer or filter). So one tab is one ffmpeg run, with its crop fixed at the starting `s`. While the panel sits at a *different* `s` (the user widened or narrowed it), Python reshapes each written frame to the starting geometry with PIL. That costs CPU only while resized, about 0.6 of a core in the worst case. A later optimisation could restart the screencast with a matching `maxWidth`; not needed now.
4. **Place frames with `ceil`, not `floor`**, at 25 fps from `start_wall_ms`. Floor showed content up to a frame early; ceil brought the median error below today's recorder.
5. **Pump the sync API.** Playwright's sync API dispatches CDP events only inside Playwright calls, so `time.sleep` polling never sees the marker page. Use `context.wait_for_event("page", timeout=100)` or `page.wait_for_timeout`.
6. **Popup bridge quirk is parity, not a regression.** In both today's recorder and the panel, a popup's first document inherits `__SKILL_BRIDGE_V1__` from its initial `about:blank`, so the bridge early-returns. The init script still runs, and the recorder's existing `_ensure_bridge_installed_sync` backstop re-installs it. Panel-side script injection (`Page.addScriptToEvaluateOnNewDocument` via `webContents.debugger`) is therefore unnecessary.
7. **Video start = the moment the screencast starts**, which is more exact than today's registration-time stamp. The `tab_meta.video_start_wall_ms` convention (event wall − tab video start) is unchanged.

## Not tested here (left for Gate B / manual)

- A 150% display: tested only at 125%. The crop is expressed relative to frame size, so other D values should behave the same.
- A 2-minute run: CPU was measured on 12 s runs and scales linearly.
- `alert`/`confirm` through the dialog override inside the panel. The override script installs; the DialogSyncServer round trip is exercised in Gate B.

## Phase 2 follow-up (real `browser_panel.js` in Electron 42, 2026-10-20)

Smoke-tested the shipped panel code (not the harness) with Playwright attached: a `record` run with a 1280×720 viewport; panel widths 820 / 1100 / 1350 → scale 0.64 / 0.86 / 1; `innerWidth/innerHeight/DPR` 1280/720/1 on the first page, in a site-opened popup, and after a cross-origin navigation; the popup's `page.opener()` and `postMessage` work; a popup's `window.close()` drops its tab; `close_run` wipes the run.

Two new findings:
- **Attaching `webContents.debugger` to a view that has never navigated crashes Electron** (crashpad, no message). The panel attaches right after `loadURL(marker)`.
- **Playwright's own mouse input misses on a shrunk page** (scale < 1): its click point lands on a different element. Human input through the view is unaffected, and recording only uses human input. Anything that must click with Playwright on a record run (tests, Gate B) uses `scale = 1` or scaled `sendInputEvent`.

## Gate B — equivalence with the launched recorder (2026-10-20)

Same scripted session recorded three times on the fixture: today's launched recorder (Playwright input), and embedded in the real panel at 1300 px (scale 1) and 820 px (scale 0.64) wide (Electron `sendInputEvent`, i.e. a person's input path). Twelve steps: click + type in an input, click a button, click + type in a same-origin iframe, click in a cross-origin iframe, a download, a `window.open` popup, click + type in the popup.

| Check | Result |
|---|---|
| `events.jsonl` (action, selectors, identity, `frame.chain`, tab id / `opened_by` / opener, bbox ±1 px, viewport) | **Equal**, apart from the fixture's animated clock inside `ancestors[].outer_html`, and the two pre-existing races below. |
| Downloads | Saved to the session's `downloads/` in all three. |
| Frames (`extract_frames_for_session`, element crops) | All cut, no failures. Crops show the same element: mean abs diff 2–11 /255 at scale 1 (29 on typing steps — text caught mid-entry), 6–36 at scale 0.64 (softer: the view has fewer pixels). |
| Compile (fake LLM router, as the test suite uses) | Same 14 steps; only `snapshot_dom_hash` differs (the clock). The popup page, which has no clock, hashes identically. |
| `environment.json` | 1280×720 and the same DPR in all three. |

Found and fixed on the way:
- **Frames in the real panel are the view's size, with the page filling them** (`deviceWidth` in frame metadata = the view's DIP width), not the full-size frame with the page in a corner that the Gate A harness produced. `ScreencastVideo` now derives the page's share of the frame from the panel's scale *and* `metadata.deviceWidth`, so it handles both.
- **Download vs. its click order.** Electron announces a download sooner than a launched Chromium, while `bridge.js` holds the click's report up to `click_settle_max_ms`; the panel's file hand-over now keeps Playwright pumping for 0.75 s so the click lands first.
- **Scripts per page, not per context.** The plan's context-level `add_init_script` + origin guard was replaced by `page.expose_binding`/`add_init_script` on each run page, so Studio's window and other runs never see the recorder at all. Run pages are identified by devtools target id, which the panel reads through `webContents.debugger` (`targets` op) — this also covers tabs opened with `noopener` (most `target=_blank` links), which `page.opener()` can't link back.

Pre-existing races, present in the launched recorder too (four launched runs: download recorded after the *next* click 2/4, popup event stamped with the popup's tab 1/4) — tracked as BUILD-47.

**Outcome:** equivalent. `SKILL_RECORD_IN_PANEL` stays off until the recording UI (BUILD-46 b) exists, since without it a panel recording would be shown with the test run's UI.

## Phase 4 check — sign-in in the panel (2026-10-20)

The same harness (real `browser_panel.js` + `browser_control.js` in Electron), with a sign-in (`auth_mode`) session on the fixture's login page: a person's click signs in, then `learn_auth` runs, then stop.

| Check | Result |
|---|---|
| Run kind | `signin`; the page fills the pane (no fixed viewport) |
| learn_auth LIVE capture | Ran in a background tab of the same run (2 tabs seen, the extra one closed afterwards); its cookie names came from the run's partition |
| Saved sign-in file | Cookie `sid` and `localStorage` `tok` for the fixture origin — nothing from the Studio window |
| Stop | Run closed, recorder reports the browser closed, no recorder errors |

The learned definition failed its self-test, as expected: the fixture page has no sign-in signals (no password box, no markers) for the learner to key on. That is the learner's verdict on the page, not a panel problem — the LIVE capture itself succeeded.

**Outcome:** `SKILL_RECORD_IN_PANEL` is now on by default, with the recording UI in place (BUILD-46 resolved; follow-ups in BUILD-48).
