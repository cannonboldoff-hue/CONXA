# Shipping Guide — Dev/Prod Isolation & Runtime

This guide is written in plain language. It lists everything we can ship from the
Dev/Prod isolation work (and the runtime, installer, and update features it touches),
and for each one tells you **what it is**, **how to ship it**, and **how to test it**.

---

## The golden rule

> **Build and test everything in Dev first. Production only ever receives a release that
> was already tested in Dev — and it gets the *exact same file*, never a rebuild.**

Everything below is designed around that one rule. Dev and Prod live in separate folders,
talk to separate clouds, and receive separate updates, so you can break things in Dev all
day without ever touching the live Production system.

---

## How to go about it (the order to ship in)

Do it in this order — each step builds on the one before:

1. **Turn on the switch locally.** Copy `.env.dev.example` → `.env.dev`, fill in your keys,
   and run things with `make dev-...`. Confirm Dev uses its own folders. *(Features 1–4)*
2. **Point Dev at a cloud.** Start with the local backend (`make dev-backend`). Later,
   optionally stand up the hosted Dev tier on Render. *(Features 5, 13)*
3. **Set up the update channels + CI secrets.** Add the Dev cloud variables to GitHub so
   dev builds publish to the `dev` channel. *(Features 6, 7, 12)*
4. **Build and install a Dev installer.** Confirm it lands in the Dev folder, registers a
   separate Claude Desktop entry, and talks only to the Dev cloud. *(Features 8, 9)*
5. **Run the full workflow in Dev.** Record → compile → test in the sandbox → install →
   execute in Claude Desktop → watch auto-update. *(Features 10, 11)*
6. **Promote to Production.** Once Dev is proven good, run the promotion workflow. *(Feature 12)*

A one-page checklist is at the very bottom.

---

## Feature 1 — The single switch (`CONXA_ENV`)

**What it is.** One setting, `CONXA_ENV`, that can be `dev` or `prod`. Flip it and everything
else follows: which settings file loads, which folders are used, which cloud is contacted,
and which updates you receive. If you don't set it, the safe default kicks in (Dev on your
own machine; Prod for a shipped runtime/installer).

**How to ship it.** Nothing to deploy — it's the foundation the rest sits on. Just make sure
everyone knows: set `CONXA_ENV=dev` for development work, `CONXA_ENV=prod` for the live
system. The launcher (Feature 4) sets it for you.

**How to test it.**
- Run `make dev-env` → it should print `dev`, folders under `~/.conxa-dev`, channel `dev`.
- Run `make prod-env` → it should print `prod`, folders under `~/.conxa`, channel `stable`.
- Safety check: try to start the backend as Production with login turned off — it must
  refuse to start:
  ```bash
  CONXA_ENV=prod SKILL_AUTH_REQUIRED=false make prod-backend   # should fail fast with a clear message
  ```

---

## Feature 2 — Separate settings files (`.env.dev` / `.env.prod`)

**What it is.** Two separate settings files so Dev secrets and Prod secrets never mix. The
right one loads automatically based on `CONXA_ENV`. There are ready-to-copy templates.

**How to ship it.**
- Copy `.env.dev.example` → `.env.dev` and fill in Dev values (local URLs, test keys).
- Copy `.env.prod.example` → `.env.prod` and fill in Production values.
- For the website, do the same with the templates in `conxa-cloud/frontend/`.
- The real `.env.dev` / `.env.prod` files are git-ignored, so secrets are never committed.

**How to test it.**
- Put a distinctive value (e.g. a fake `CONXA_CLOUD_API`) in `.env.dev` only, start Dev, and
  confirm it's used. Start Prod and confirm it is **not** used.
- Confirm `git status` never shows `.env.dev` or `.env.prod` (they're ignored), but the
  `.example` files are tracked.

---

## Feature 3 — Separate folders on one machine

**What it is.** Dev and Prod keep all their files apart, so they can both be installed at
once without stepping on each other:

| | Dev | Prod |
|---|---|---|
| Runtime files | `~/.conxa-dev` | `~/.conxa` |
| Runtime data/logs | `Conxa-Dev` (in AppData) | `Conxa` |
| Build Studio state | `~/.conxa-build-studio-dev` | `~/.conxa-build-studio` |

**How to ship it.** Automatic once the switch is set — the launcher and installers put files
in the right place. Nothing extra to deploy.

**How to test it.**
- Run `make dev-runtime` and `make prod-runtime` (or the studio) and confirm two separate
  folders appear. Logs, skill packs, and caches should be under the matching folder.
- Delete the whole `~/.conxa-dev` folder — Production (`~/.conxa`) should be completely
  unaffected.

---

## Feature 4 — The launcher (`conxa.sh` / `conxa.ps1` / `make`)

**What it is.** One command to start any piece in the environment you pick, so nobody has to
remember a dozen settings.

**How to ship it.** Already in the repo (`scripts/conxa.sh`, `scripts/conxa.ps1`, `Makefile`).
On Mac/Linux, `chmod +x scripts/conxa.sh` once. Tell the team the commands.

**How to test it.**
```bash
./scripts/conxa.sh dev studio      # or: make dev-studio
./scripts/conxa.sh dev backend     #     make dev-backend
./scripts/conxa.sh prod backend    #     make prod-backend
```
```powershell
.\scripts\conxa.ps1 dev studio      # Windows
```
Each should print the environment banner (folders + channel) before launching. Run
`make dev-env` / `make prod-env` for a dry run that prints and exits.

---

## Feature 5 — Separate cloud endpoints

**What it is.** Dev talks to a Dev cloud (your local machine by default, or a hosted Dev
tier); Prod talks to the live cloud (`apis.conxa.in`). They never cross.

**How to ship it.**
- Local Dev: `make dev-backend` runs the cloud on `127.0.0.1:8000` with a filesystem
  database and login turned off — zero setup.
- Hosted Dev (optional): see Feature 13.
- Prod: already runs on Render; just make sure `.env.prod` / Render has the live values.

**How to test it.**
- Start `make dev-backend`, open `http://127.0.0.1:8000/healthz` → should say healthy.
- Open `http://127.0.0.1:8000/readyz` → should report `filesystem` (local) or `up` (if you
  set a Dev database).
- Confirm the Studio in Dev mode calls the Dev URL, not `apis.conxa.in` (watch the logs).

---

## Feature 6 — Update channels (`dev` vs `stable`)

**What it is.** Two separate "update tracks." Dev builds go on the `dev` track; tested,
promoted builds go on the `stable` track. A Dev runtime only ever sees `dev`; a Production
runtime only ever sees `stable`. So an untested Dev build can never reach a real customer.

**How to ship it.** Already built into the cloud. The update address now accepts a channel:
`GET /api/v1/manifest.json?channel=dev` or `?channel=stable` (defaults to `stable`). Each
runtime asks for its own channel automatically based on `CONXA_UPDATE_CHANNEL`.

**How to test it.**
- Publish a fake Dev version and confirm it shows up on `dev` but **not** on `stable`:
  ```bash
  # (admin token required) publish to dev
  curl -X POST "$DEV_API/api/v1/admin/component-versions/conxa_app?channel=dev" \
       -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
       -d '{"version":"app-v9.9.9-dev.1","min_host":"host-v1.0.0","files":[...]}'
  curl "$DEV_API/api/v1/manifest.json?channel=dev"     # shows app-v9.9.9-dev.1
  curl "$DEV_API/api/v1/manifest.json"                 # stable — does NOT show it
  ```
- Ask for a made-up channel (`?channel=bogus`) → should return an error (HTTP 400).

---

## Feature 7 — Auto-update (host + app layers) on the right channel

**What it is.** The runtime updates itself in two pieces — a big "host" program (rare) and a
small "app" layer (frequent). With channels, a Dev runtime pulls Dev updates and a Prod
runtime pulls stable updates. Updates are signed, so a tampered update is rejected.

**How to ship it.** No extra work beyond Features 6 and 12 — the runtime already reads its
channel and verifies the signature.

**How to test it.**
- Install a Dev runtime, publish a newer app version on the `dev` channel, then start the
  runtime and watch it download and switch over.
- Break the update on purpose (wrong checksum) and confirm the runtime refuses it and keeps
  running the old version (safe rollback).
- Confirm a Prod runtime ignores the Dev update entirely.

---

## Feature 8 — Separate Claude Desktop entry per environment

**What it is.** A Dev installer registers itself in Claude Desktop as **`conxa-dev`**, while
Prod registers as **`conxa`**. This means you can have both installed and switch between them
inside Claude Desktop without conflict.

**How to ship it.** Automatic — the installer picks the name and folder based on the
environment it was built in.

**How to test it.**
- Build and run a Dev installer, then open Claude Desktop's config
  (`claude_desktop_config.json`). You should see a `conxa-dev` server entry whose settings
  point at the `.conxa-dev` folder and carry `CONXA_ENV=dev` + `CONXA_UPDATE_CHANNEL=dev`.
- Install a Prod build too and confirm both `conxa` and `conxa-dev` entries exist side by
  side and each uses its own folder.

---

## Feature 9 — Installers embed the right environment

**What it is.** When you build a customer installer, the "phone-home" addresses baked into it
(where it syncs skills and sends usage) match the cloud you built it against. A Dev installer
talks to the Dev cloud; a Prod installer talks to Prod. Both addresses now always agree.

**How to ship it.** Build installers in the right environment: build in Dev (`CONXA_ENV=dev`)
for testing, and in Prod for real releases. The Build Studio does this automatically based on
its environment.

**How to test it.**
- Build a test installer in Dev, open the generated `pack.json`, and confirm both
  `sync_endpoint` and `tracking_url` point at the **Dev** address (never `apis.conxa.in`).
- Build one in Prod and confirm both point at the live address.

---

## Feature 10 — Test skill packs in the sandbox before shipping

**What it is.** The Build Studio has a built-in sandbox that mimics a real customer machine,
so you can run a compiled skill pack end-to-end before anyone installs it. With isolation, the
sandbox uses the Dev tree and never self-updates, so tests are clean and repeatable.

**How to ship it.** Already part of the Studio. Use it as the last check before building an
installer.

**How to test it.**
- In Dev, record a workflow → compile it → run it in the sandbox → confirm the steps execute
  and pass. Only build an installer after the sandbox run is green.

---

## Feature 11 — Execution & recovery, tested safely in Dev

**What it is.** When a skill runs, the runtime tries to recover from small changes on the page
on its own (the "recovery tiers"). You can test this in Dev without any risk to Production.

**How to ship it.** No change to how recovery works — this is about being able to exercise it
in the isolated Dev environment.

**How to test it.**
- Run a skill in Dev against a page that changed slightly and confirm the runtime recovers.
- Run the runtime's own checks (in `runtime/test/`) — for example the resolver and recovery
  tests — before shipping a new app layer.

---

## Feature 12 — Ship a runtime release (direct to stable)

**What it is.** A clean semver tag (`app-vX.Y.Z` / `host-vX.Y.Z`) builds in CI and, on
success, posts straight to the **stable** channel on the prod cloud. There used to be a
Dev-channel staging step + a `promote-release.yml` promotion workflow, but the hosted Dev
cloud service was removed (commit `91eebe8`) — the Dev lane now runs only on
`127.0.0.1:8000`, which a GitHub-hosted runner can't reach, so there's no Dev target left to
stage a build on before promoting it. `promote-release.yml` has been deleted accordingly.

**How to ship it.**
1. Tag a clean release, e.g. `git tag app-v1.3.0 && git push --tags`. CI builds it and, if
   `CLOUD_API_URL`/`CLOUD_ADMIN_TOKEN` are configured, posts the manifest record straight to
   `stable`.
2. Confirm the version now appears on `GET /api/v1/manifest.json` (stable, no `?channel=`).

**How to test it.**
- Confirm a Production runtime now offers the update.
- If you need to test a build before it's customer-visible, point a local runtime at your own
  `127.0.0.1:8000` dev backend and post the manifest record there by hand — there is no
  automated Dev channel in CI to do this for you right now.

---

## Step-by-step: shipping conxa-app, conxa-runtime, and Build Studio

This is Feature 12 above, written out as a full walkthrough for each of the three things you
can actually ship a change in.

### conxa-app (and conxa-runtime/host, if that changed too) — direct-to-stable pipeline

1. **Commit your change** to whatever branch, then merge to main.
2. **Cut a clean release tag** and push it:
   ```bash
   git tag app-v1.3.0
   git push origin app-v1.3.0
   ```
   (For a host-layer change, use `host-v1.3.0` instead — same pattern. A `-dev.N`/`-beta.N`/
   `-rc.N`/`-alpha.N` suffix still marks the GitHub Release as a prerelease, but no longer
   routes anywhere different — there is no separate Dev channel in CI to route to.)

   Pushing this tag triggers `build-runtime-app.yml` (or `build-runtime-host.yml` for the
   host), which builds, obfuscates, zips, publishes the GitHub Release, and — if
   `CLOUD_API_URL`/`CLOUD_ADMIN_TOKEN` are configured — posts the manifest straight to
   **stable**. `runtime/test/gate_replay.js` (real skill replay against the `MIN_HOST` exe)
   still runs in CI before the release/publish steps and fails the build if it doesn't pass —
   this is now the only pre-prod gate. If it goes red, check `MIN_HOST` in
   `build-runtime-app.yml` first: it's the compatibility promise stamped into `version.json`,
   and a stale value fails the replay before it fails a customer.
3. **Production runtimes pick it up automatically** on their next update poll — signature
   verified against the baked-in public key, SHA-256 verified, then `bootstrap.js` does the
   atomic swap with rollback if anything goes wrong.

### conxa-runtime (the host exe) — when and how to ship it

The "host" is the small `conxa-runtime.exe` that Claude Desktop actually spawns. It only
contains `bootstrap.js` + `_pkg_stubs.js`, built with `@yao-pkg/pkg` using `--no-bytecode`
(never turn bytecode back on — V8 `.jsc` masks the Node version and segfaults the Playwright
selector engine inside a pkg-bundled binary). Its only job is to read `version.json`, check
`min_host` compatibility, and load the real logic from the disk-resident app layer
(`conxa-app/`, shipped separately — see the section above). Everything that actually changes
week to week (recorder, resolver, recovery, MCP tools) lives in `conxa-app`, not here.

**You only need to touch the host when `bootstrap.js`, `_pkg_stubs.js`, or the pkg bundling
config itself changes** — new stubbed dependency, a `min_host` semver bump, a change to how
the host locates/loads the app layer, etc. Ordinary feature work never requires a host release.

1. Commit your change, merge to main.
2. Cut a clean release tag the same way as conxa-app, just with the `host-` prefix:
   ```bash
   git tag host-v1.1.0
   git push origin host-v1.1.0
   ```
   This triggers `build-runtime-host.yml`, which builds the exe with `--no-bytecode`,
   publishes the GitHub Release, and — if `CLOUD_API_URL`/`CLOUD_ADMIN_TOKEN` are configured —
   posts the manifest straight to **stable**.
3. **Test it before customers see it, if you need to.** Point a local runtime at your own
   `127.0.0.1:8000` dev backend and post the manifest record there by hand — install a runtime
   against it, let it self-update: on startup the app layer checks the manifest, and if
   `conxa_runtime` is newer it downloads the new host files, runs `--selfcheck` against the new
   exe, then `activate()`s it — this never touches the *running* process's own file, so the
   swap takes effect on the next launch. Confirm Claude Desktop can still spawn it and the MCP
   handshake still works.
4. Production runtimes pick it up on their next update poll, same as app-layer updates.

**Two things that make the host different from conxa-app in practice:**
- New customer installers embed the host exe directly, so a customer who installs fresh
  always gets whatever host version is live on `stable` at build time — you don't need a host
  release just to get a fix to new installs, only to update machines that installed earlier.
- Because host releases are rare and load-bearing (everything else depends on it booting
  correctly), always confirm the built exe still launches with `--no-bytecode` intact before
  tagging a promotion — check the CI build log for the pkg command, don't just trust the tag.

### Build Studio — no promotion step, this one is manual

There is no dev/stable manifest system for Build Studio. It ships differently:

1. Commit your change.
2. Tag it:
   - `studio-v1.2.3-beta.1` to test — this marks the GitHub Release `prerelease`, so it will
     **not** reach the stable auto-update channel.
   - `studio-v1.2.3` for a real release.

   Either tag triggers `build-studio.yml`, which runs `electron-builder publish` straight to a
   GitHub Release.
3. **To make it live**, you must manually update Render env vars on the `conxa-api` service:
   `CONXA_STUDIO_VERSION`, `CONXA_STUDIO_WIN_URL`, `CONXA_STUDIO_WIN_SHA256`, and
   `CONXA_STUDIO_WIN_SHA512`. The `/api/v1/updates/studio-manifest` endpoint — the one the
   dashboard and the Studio's own self-updater read from — is driven entirely by these env
   vars. Nothing promotes automatically the way conxa-app/conxa-runtime do.

**Order matters when the compile prompt changes.** The prompt Studio sends at compile time (the "can this be
undone?" definition, `_EFFECTS_RULE`) lives in the shared core that the **cloud** also runs. Deploy the cloud first,
then release the Studio that depends on it: against an old cloud the model is never asked the question, so every
click counts as "cannot be undone" and is asked about.

### Conxa Execute — no promotion step, this one is manual

Same shape as Build Studio, its own tag prefix and env vars:

1. Commit your change.
2. Tag it:
   - `execute-v0.1.1-beta.1` to test — marks the GitHub Release `prerelease`, so it will
     **not** reach the stable auto-update channel.
   - `execute-v0.1.1` for a real release.

   Either tag triggers `build-execute.yml`, which runs `electron-builder publish` straight
   to a GitHub Release.
3. **To make it live**, manually update Render env vars on the `conxa-api` service:
   `CONXA_EXECUTE_VERSION`, `CONXA_EXECUTE_WIN_URL`, `CONXA_EXECUTE_WIN_SHA256`, and
   `CONXA_EXECUTE_WIN_SHA512`. The `/api/v1/updates/execute-manifest` endpoint and the
   `/api/v1/updates/execute/latest.yml` feed the installed app's own silent updater reads
   are driven entirely by these env vars.

### If the source repo goes private, does any of this change?

No — same tags, same triggers. The one thing that
breaks is the **URLs these steps produce**. `build-runtime-host.yml`/`build-runtime-app.yml`
and the cloud's `_release_url()` helper both build links like
`https://github.com/<repo>/releases/download/...`, which become authenticated-fetch-only once
the repo is private — a plain customer machine with no GitHub token gets a 404/401. That's the
exact problem `research-analysis/ops/private-repo-migration.md` covers. Fix it once (point releases
at a Conxa-owned artifact base instead of GitHub Releases) and this shipping process is
otherwise unaffected by repo visibility.

---

## Feature 13 — Hosted Dev cloud tier (optional)

**What it is.** A second cloud on Render that mirrors Production, for full end-to-end testing
of the real login and database paths — completely separate from Prod (its own service, its own
database, the `dev` update channel).

**How to ship it.** Only when you want it. Deploy `conxa-cloud/render.dev.yaml` as a separate
Render blueprint. Point your Dev settings at `dev-apis.conxa.in` instead of localhost.

**How to test it.**
- Hit the Dev tier's `/healthz` and `/readyz` and confirm it's healthy and using its **own**
  database (not Production's).
- Log in with a Dev account and confirm it never appears in Production.

---

## Feature 14 — Safety guards

**What it is.** Two guards that prevent dangerous mistakes:
- A Production-labeled server **refuses to start** if login protection is off, or if any
  required Production setting is missing. The Render config now lists every required value so
  nothing boots half-configured.
- A bug where the Dev runtime could save login tokens into the Production folder is fixed —
  tokens now always land in the matching environment's folder.

**How to ship it.** Already in place. Just make sure Production's Render dashboard has all the
values marked `sync: false` in `render.yaml` filled in.

**How to test it.**
- Start Prod with a required value missing → it should refuse to start and name what's missing.
- In Dev, trigger the token-saving path and confirm the token file appears under `.conxa-dev`,
  not `.conxa`.

---

## Pre-flight checklist (before shipping to Production)

- [ ] `.env.dev` and `.env.prod` filled in; real files are git-ignored.
- [ ] `make dev-env` and `make prod-env` print the correct, separate folders + channels.
- [ ] Dev backend runs and `/healthz` + `/readyz` look right.
- [ ] GitHub has the Production variables/secrets (`CLOUD_API_URL`, `CLOUD_ADMIN_TOKEN`), plus
      the manifest signing key (server-side) and public key (repo variable). There is no hosted
      Dev cloud to configure — the Dev lane runs on `127.0.0.1:8000` only.
- [ ] A Dev installer installs into `~/.conxa-dev`, shows as `conxa-dev` in Claude Desktop,
      and its `pack.json` points only at the Dev cloud.
- [ ] Full Dev run passes: record → compile → sandbox test → install → execute → auto-update.
- [ ] A clean release tag posts straight to `?channel=stable` — check `GET
      /api/v1/manifest.json` shows the new version and matching checksum.
- [ ] Production server refuses to start if a required value is missing (guard works).

---

## Quick command reference

```bash
# Start pieces in an environment
make dev-studio        make prod-studio
make dev-backend       make prod-backend
make dev-frontend      make prod-frontend
make dev-runtime       make prod-runtime
make dev-env           make prod-env        # dry run: print settings and exit

# Release
git tag app-v1.3.0 && git push --tags     # build + publish straight to stable

# Check what stable is serving
curl "https://apis.conxa.in/api/v1/manifest.json"
```

For the full technical design, see `docs/TRD.md` → "Dev/Prod Environment Isolation".
