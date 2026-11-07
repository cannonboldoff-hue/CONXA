# Runner Machine Profile (PROD-5)

How to turn an always-on Windows machine or VM into a Conxa **runner** that executes scheduled
skills around the clock — reaching hundreds to thousands of runs per day on customer-owned
hardware, with no chat application open and no data ever leaving the machine.

This is the same unattended-automation model established RPA vendors deliver, which is exactly
why enterprises already trust it. It is also the sanctioned Horizon-2 direction (`docs/PRD.md`
§14.5, decided 2026-08-21): execution workers run on customer infrastructure; Conxa's cloud
holds neither work items nor schedules.

---

## 1. Machine setup checklist

Run these on the machine (or hand the commands to IT):

| Step | Command | Why |
|---|---|---|
| Install the runtime | Conxa installer | Registers MCP hosts, stages Chromium, syncs skill packs |
| Sign in once | any chat host → `get_runtime_status` | Seeds the sync token + target-app sessions |
| Register autostart | `conxa-runtime.exe runner setup` | Startup-folder entry launches `schedule daemon` at sign-in |
| Never sleep on AC | `powercfg /change standby-timeout-ac 0` | A sleeping runner misses schedules |
| Never hibernate on AC | `powercfg /change hibernate-timeout-ac 0` | Same |
| Screen off is fine | `powercfg /change monitor-timeout-ac 15` | Runs are headless; the monitor is irrelevant |
| Keep sessions warm | sign in to each target app once | Sessions are reused and refreshed on every successful run |
| Verify everything | `conxa-runtime.exe runner doctor` | Chromium, packs, keychain, cron validity, locks dir, autostart, power posture |

The daemon runs headless after sign-in; a tray icon appears for status/pause/quit
(`docs/UI-UX-Brief.md` §10). Nothing else needs a desktop session beyond the initial logins.

## 2. Capacity math

Per machine:

```
parallel slots   = CONXA_MAX_CONCURRENT_RUNS   (default 5)
run wall budget  = CONXA_EXECUTION_DEADLINE_MS  (default 210s)
theoretical max  = 5 × (86400 / 210) ≈ 2,000 runs/day
realistic        = dominated by average real run time, e.g.
                   5 parallel × 90s runs ≈ 4,800/day… capped by platform politeness,
                   login-gated flows, and recovery pauses — plan on hundreds to low thousands.
```

Rules of thumb:

- **Scale horizontally**: N runner machines ≈ N × the above. Each machine is sovereign — there
  is no fleet coordinator, by design.
- **Partition targets across machines yourself.** The "never two skills against the same app at
  once" rule is enforced per machine (in-process map + cross-process lock files). Two machines
  pointed at the *same* Render/Vercel/etc. account can still overlap — give each machine its own
  set of target platforms/accounts if overlap matters to you.
- **Burst is not what runners are for.** A runner is capacity you set up in advance; it cannot
  absorb a 10,000-request spike like an API can. Steady volume is the sweet spot.

## 3. Scheduling patterns that work well

- **Off-peak windows**: cron like `0 1-5 * * *` keeps heavy batches inside quiet hours.
- **Stagger same-platform skills** across minutes (`0 6 * * *`, `30 6 * * *`) even on one
  machine — the runtime will serialize them anyway, but staggering avoids long lock waits
  eating each run's deadline budget.
- **Generous grace for flaky hours**: `--grace 120` on early-morning schedules lets a slow boot
  still catch up, while stale week-old slots are always skipped, never burst-fired.
- **One schedule per outcome**, not one mega-schedule: individual records fail/retry/skip
  independently and read clearly in `schedule list`.

## 4. Honest limits

- **Session lifetime is the unattended ceiling.** When a target app's login dies overnight, the
  run fails fast at pre-flight — it opens **no** sign-in window on the runner (nobody is there
  to use it) — and the schedule's last-run message (`list_schedules`) names the app that needs
  a person. Someone must re-authenticate on the machine before the next success. Vendor-controlled
  long-lived sessions (PROD-4 / PRD §14.5 question 5, still open; see §4a) are the structural fix.
- **Runner machines need someone occasionally.** Updates, re-auths, and the occasional reboot
  still involve a human; "runs while you sleep" ≠ "no ops ever."
- **Same-machine concurrency cap is flat (5).** Memory-derived sizing was deliberately deferred
  (see `RT-3-CAP-SIZING` in TODO.md); raise `CONXA_MAX_CONCURRENT_RUNS` on roomy VMs if you
  know what you're doing.

### 4a. Longer-lived sessions for your own automation traffic (vendor guidance)

Conxa cannot lengthen a login it does not control — only the app's owner can. If you own the
software being automated, you can set a sensible policy for your *own* runner machines, which
is an app owner configuring their own product, not circumventing MFA:

- **Longer session lifetime for the runner's account** — a dedicated service/automation user
  with a session (or refresh-token) lifetime measured in days or weeks instead of hours.
- **Device-locked sessions** — bind that account's sessions to the runner machine (device or IP
  allow-list, device-bound tokens) so a long lifetime is safe: a stolen cookie is useless
  elsewhere.
- **A dedicated account per runner**, so its policy never loosens anyone's interactive login.
- **Keep MFA on for humans.** The goal is one MFA at setup and then a long-lived session, not
  removing the second factor.

Nothing changes in Conxa when you do this: the runtime already reuses a valid session and only
asks for a person when one fails its pre-flight probe. If it does fail, an unattended run reports
which app needs signing in rather than opening a window (§4).

## 5. Where things live on the runner

All under the writable data dir (`%APPDATA%\Conxa` on Windows prod installs):

```
scheduler/
  schedules/*.json    encrypted schedule records   (schema: docs/Backend-Schema.md §1.4)
  state.json          live status snapshot         (tray + CLI read this)
  logs/               daily scheduler logs
  commands/           pause/resume/run_now/quit inbox
locks/*.lock          cross-process platform mutexes
logs/runtime.log      engine log incl. trigger:"scheduled" execute_start lines
```

Diagnostics order when something didn't run: `runner status` → `runner doctor` →
`scheduler/logs/` → `logs/runtime.log`.
