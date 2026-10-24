# Security Gaps

**Status:** Current as of 2026-09-02  
**Scope:** Conxa platform — Build Studio, Conxa Cloud, Runtime  
**Audience:** Internal engineering, security reviewers, auditors

This document is the detailed reference for known security gaps across all three tiers. For the high-level security model (auth mechanisms, trust boundaries, invariants) see **[`docs/TRD.md §15`](TRD.md#15-security-model)**. For general tech debt see **`docs/TRD.md §17`**.

---

## Severity Scale

| Label | Meaning |
|---|---|
| **Critical** | Exploitable without insider access; leads to auth bypass, RCE, or data breach at scale |
| **High** | Requires one additional condition (leaked secret, compromised CDN, etc.); significant blast radius |
| **Medium** | Limited blast radius or requires attacker to already have partial access |
| **Low** | Defence-in-depth weakness; no realistic exploit path in current deployment |

---

## Summary Table

| ID | Title | Tier | Severity | File(s) |
|---|---|---|---|---|
| [SG-01](#sg-01-rbac-not-enforced-on-routes) | RBAC not enforced on routes | Cloud API | High ✅ Fixed | `app/services/rbac.py`, `app/api/` |
| [SG-02](#sg-02-proxy-identity-bypass) | Proxy identity bypass via shared header secret | Cloud API | High ✅ Fixed | `app/services/saas.py` |
| [SG-03](#sg-03-x-forwarded-host-not-sanitised) | X-Forwarded-Host not sanitised in `_api_base()` | Cloud API | Medium ✅ Fixed | `app/api/publish_routes.py` |
| [SG-04](#sg-04-in-memory-rate-limit-on-delta-endpoint) | In-memory rate limit cleared on restart | Cloud API | Medium ✅ Fixed | `app/api/skillpack_update_routes.py` |
| [SG-05](#sg-05-tracking-hmac-secret-is-optional) | Tracking HMAC secret optional — fallback accepts any token | Cloud API | Medium ✅ Fixed | `app/api/tracking_routes.py` |
| [SG-06](#sg-06-telemetry-payload-unbounded) | Telemetry payload unbounded — no event count or field size cap | Cloud API | Medium ✅ Fixed | `app/api/tracking_routes.py` |
| [SG-07](#sg-07-installer-download-is-fully-public) | Installer download is fully public | Distribution | Medium ✅ Fixed | `app/api/publish_routes.py` |
| [SG-08](#sg-08-sync-token-is-a-shared-installer-secret) | Sync token is a shared installer secret | Distribution | Low | `app/api/skillpack_update_routes.py`, `runtime/sync.js` |
| [SG-09](#sg-09-no-code-signing-on-self-update-binary) | No code signing on self-update binary | Runtime | High ⚠️ Partially Fixed | `runtime/manifest_manager.js` |
| [SG-10](#sg-10-update-bat-uses-mathrandom-for-temp-filename) | Update `.bat` uses `Math.random()` for temp filename | Runtime | Low ✅ Resolved (mechanism removed) | `runtime/manifest_manager.js` |
| [SG-11](#sg-11-plaintext-session-fallback-is-silent) | Plaintext session fallback is silent on keytar failure | Runtime | Medium ✅ Fixed | `runtime/auth_manager.js` |
| [SG-12](#sg-12-company-name-used-in-file-paths-without-re-validation) | Company name used in file paths without re-validation | Runtime | Low | `runtime/auth_manager.js`, `runtime/sync.js` |
| [SG-13](#sg-13-no-per-user-identity-at-runtime) | No per-user identity at runtime — `uid` is spoofable | Runtime | Low | `runtime/server.js`, `runtime/tracker.js` |
| [SG-14](#sg-14-byok-key-at-rest) | Enterprise BYOK key encryption depends entirely on one env var | Cloud API | Medium | `app/services/byok.py` |
| [SG-15](#sg-15-machine-fingerprint-handling) | Machine-fingerprint handling — hash-only by design, still a stable per-device identifier | Cloud API | Low | `app/api/machine_binding.py`, `app/services/entitlements.py` |
| [SG-16](#sg-16-analytics-retention-as-data-minimisation) | Analytics retention is a billing control, not (yet) enforced deletion | Cloud API | Low | `app/services/tracking.py` |
| [SG-17](#sg-17-starter-external-distribution-is-detectable-not-blocked) | Starter external distribution is detectable, not blocked | Distribution | Low (by design) | `app/api/publish_routes.py` |
| [SG-18](#sg-18-seat-limit-not-enforced) | Seat limit not enforced — team invites bypass the backend entirely | Cloud API | Low ✅ Fixed | `app/services/entitlements.py`, `app/api/deps.py` |
| [SG-19](#sg-19-one-signing-key-now-covers-two-artifacts-no-rotation) | One Ed25519 signing key now covers two artifacts, no key rotation | Cloud API / Runtime | Low | `app/api/manifest_signer.py`, `runtime/app/manifest_manager.js`, `runtime/app/policy_gate.js` |
| [SG-20](#sg-20-popup-shape-telemetry-app_obs-leaves-the-customer-machine) | Popup-shape telemetry (`app_obs`) leaves the customer machine | Runtime / Cloud API | Low (mitigated) | `runtime/app/scrub.js`, `runtime/app/app_knowledge.js`, `app/api/app_graph_routes.py` |

---

## SG-01 — RBAC Not Enforced on Routes

**Severity:** High  
**Component:** Cloud API — `app/services/rbac.py`, `app/api/`

### Description

`app/services/rbac.py` exposes only `require_admin()`, which checks that a `Principal`'s role is `"admin"` or `"owner"`. As of this writing, this function is called in **exactly one place**: the subscription-creation route (`app/api/razorpay_routes.py` at the time of writing; the payment gateway has since moved to Cashfree — `app/api/cashfree_routes.py`). Every other authenticated endpoint enforces workspace membership (`ensure_principal`) and, for mutation endpoints, workspace ownership of the target slug (`_assert_owner`), but not role-based access.

Consequence: any authenticated workspace `"member"` can publish skill packs, upload installers, read all tracking data, and consume LLM quota — all operations that should be gated to `"admin"` or `"owner"`.

### Current Mitigation

Clerk's org membership system is the outer gate. A member first needs to be invited to the org. Seat *limits* are enforced as of 2026-08-22 (SG-18, below) at first-request time, though the invite itself still always succeeds in Clerk — there is no invite-time interception without a Clerk webhook.

### Recommended Fix

Wire `require_admin()` (or a new `require_role(principal, min_role)`) into publish, installer-upload, and installer-delete endpoints. Add role checks to LLM proxy if per-role quota tiers are needed. The scaffold is in place — it just needs to be called.

### Fix Applied

`require_admin` is now called immediately after `ensure_principal(principal)` on every mutating endpoint that previously accepted any workspace member:

- `app/api/publish_routes.py` — `post_publish()` (POST `/api/v1/workflows/publish`), `post_installer_upload()` (POST `/api/v1/workflows/{slug}/installer/upload`), `get_installer_versions()` (GET `/api/v1/workflows/{slug}/installer/versions`)
- `app/api/workflow_routes.py` — `post_create_workflow()` (POST `/workflows`), `delete_workflow_endpoint()` (DELETE `/workflows/{id}`)
- `app/api/product_routes.py` — `patch_bundle_release()` (PATCH `/packages/bundles/{slug}/release`)
- `app/api/cashfree_routes.py` — subscription creation (`create_subscription`)

Any caller whose `principal.role` is not `"admin"` or `"owner"` receives `HTTP 403`. Local dev is unaffected (the anonymous local `Principal` defaults to `role="owner"`).

> **Follow-up (2026-08-04):** enforcing this exposed a role-normalization bug. A signed-in user with **no active Clerk org** is in their own personal workspace (`personal_{user_id}`) and is its sole member, but `_normalize_org_role()` defaulted their empty role to `basic_member` — so every solo user got 403 on publish, workflow create/delete, and subscribe. It now normalizes to `owner` when `org_id` is absent, on both the trusted-proxy and Clerk-JWT identity paths. This does not widen access: a personal workspace is unreachable by any other principal. Tested in `tests/test_llm_proxy_and_publish.py`. Intentionally-public runtime phone-home endpoints (`/api/v1/telemetry/runtime-start`, the tracking-event ingest prefixes, skill-pack sync, installer downloads — see `PUBLIC_PATHS`/`PUBLIC_PATH_PREFIXES` in `app/api/security.py`) remain open by design — they carry no workspace-mutation risk and are authenticated by sync token, not Clerk role. Tested in `tests/test_product_routes.py` (member→403, admin→200).

> **Correction (2026-07-04):** this note previously referenced `run_routes.py`, which does not exist in the current codebase — corrected above to the actual public-path list in `security.py`.

---

## SG-02 — Proxy Identity Bypass

**Severity:** High  
**Component:** Cloud API — `app/services/saas.py:_trusted_proxy_identity()` (line 161)

### Description

When `SKILL_API_PROXY_SHARED_SECRET` is set and a request carries the `X-Conxa-Proxy-Secret` header with a matching value, `principal_from_request()` builds a full `Principal` from three additional headers — `X-Conxa-User-Id`, `X-Conxa-Org-Id`, `X-Conxa-Org-Role` — **without requiring a Clerk JWT**. The design intent is to let the Vercel Next.js route handler (`/app/api/v1/[...]/route.ts`) forward enriched identity claims from Clerk's server SDK.

If `SKILL_API_PROXY_SHARED_SECRET` leaks (env var exposure in Render, Vercel, or Build Studio), an attacker can impersonate any Clerk user ID with any org and role — a full authentication bypass. The secret is a single symmetric value with no rotation mechanism.

### Current Mitigation

The secret is never sent to end users or embedded in installers. Risk is limited to server-side env exposure. `secrets.compare_digest` prevents timing attacks on the comparison.

### Recommended Fix

- Rotate to a per-request HMAC signature (e.g. HMAC-SHA256 over `timestamp + user_id`) rather than a static shared secret, or
- Require a valid Clerk JWT **plus** the proxy header (the proxy path should never need to bypass JWT verification — it just needs to augment claims).
- Implement a secret rotation procedure and document it in `ROUTER_SETUP.md`.

### Fix Applied

`_trusted_proxy_identity()` in `app/services/saas.py` now supports two validation paths, tried in order:

**New HMAC path (preferred):** If both `X-Conxa-Proxy-Ts` and `X-Conxa-Proxy-Sig` headers are present:
1. Parses `X-Conxa-Proxy-Ts` as a unix timestamp (integer seconds). Rejects with `proxy_ts_invalid` if non-numeric.
2. Rejects with `proxy_ts_stale` if `|now − ts| > SKILL_API_PROXY_SIGNING_WINDOW` (default 60 s).
3. Computes `HMAC-SHA256(key=shared_secret, msg="{ts}:{user_id}")` and compares with `X-Conxa-Proxy-Sig` via `secrets.compare_digest`. Rejects with `proxy_sig_invalid` on mismatch.

The Vercel route handler must send:
- `X-Conxa-Proxy-Ts: <unix_ts>` — `Math.floor(Date.now() / 1000)`
- `X-Conxa-Proxy-Sig: <hex>` — `HMAC-SHA256(SKILL_API_PROXY_SHARED_SECRET, "{ts}:{userId}")`
- `X-Conxa-User-Id`, `X-Conxa-Org-Id`, `X-Conxa-Org-Role`, `X-Conxa-Org-Name` as before

**Legacy static-secret path (deprecated):** If the new headers are absent, falls back to the original `X-Conxa-Proxy-Secret` comparison. This path has no replay protection and should be migrated away from once the Vercel handler is updated.

New config field: `SKILL_API_PROXY_SIGNING_WINDOW` (int, default `60`).

---

## SG-03 — X-Forwarded-Host Not Sanitised in `_api_base()`

**Severity:** Medium  
**Component:** Cloud API — `app/api/publish_routes.py:_api_base()` (line 176)

### Description

`_api_base()` constructs the base URL for `sync_endpoint` and `tracking_url` — values that are written into the published `pack.json` and shipped inside installers. It reads `X-Forwarded-Proto` and `X-Forwarded-Host` from the request headers (first value before any comma), and if both are present it uses them verbatim.

If the API is ever reachable directly (without a trusted reverse proxy stripping or validating these headers), an attacker could set `X-Forwarded-Host: attacker.example.com` in a publish request. The installer's `pack.json` would then contain `sync_endpoint` and `tracking_url` pointing at the attacker's server, causing all runtimes that install this pack to exfiltrate skill-sync requests and telemetry to the attacker.

### Current Mitigation

Render's infrastructure typically forwards only its own `X-Forwarded-*` headers. In practice, the attack surface requires the attacker to also have a valid Clerk JWT to reach the publish endpoint.

### Recommended Fix

Harden `_api_base()` to only trust headers from known reverse proxies (validate against an allowlist in config), or always use a configured `SKILL_API_BASE_URL` env var and ignore forwarded host headers entirely. This is the simpler and more robust fix.

### Fix Applied

`_api_base()` in `app/api/publish_routes.py` now checks `settings.api_base_url` first. If `SKILL_API_BASE_URL` is set, it is returned directly and forwarded host headers are ignored entirely. Fallback to `X-Forwarded-Proto`/`X-Forwarded-Host` only occurs when the env var is empty (local dev / staging without explicit config).

`_validate_production_config()` in `app/main.py` now requires `SKILL_API_BASE_URL` to be set when `SKILL_AUTH_REQUIRED=true` — the backend refuses to start in production without it.

New config field: `SKILL_API_BASE_URL` (string, default `""`). Set to the canonical API origin (e.g. `https://api.conxa.in`) on Render before deploying.

---

## SG-04 — In-Memory Rate Limit Cleared on Restart

**Severity:** Medium — ✅ Fixed 2026-07-01  
**Component:** Cloud API — `app/api/skillpack_update_routes.py`

### Description

The skill-pack delta endpoint (`GET /api/v1/skill-packs/{company}/delta`) rate-limits to 1 request per 5 minutes per token. It previously used a module-level dict `_rate_cache` — in-process memory, cleared on every process restart. Render restarts the process on each new deploy and on crash recovery.

Consequence: an attacker with a valid sync token could drain the full skill pack on every process restart. On a busy service with frequent deploys this could be every few minutes.

### Current Mitigation (superseded by fix below)

Skill packs contain only compiled automation data (selectors, intents, recovery strategies) — no credentials, no secrets, no user data. The rate limit is a bandwidth/cost control, not a confidentiality control.

### Fix Applied

The rate-limit timestamp is now persisted in the existing `conxa_core.db` KV dual-store (new `rate_limits` namespace, keyed by `sha256(token)[:16]` via `_rate_limit_key()`) whenever a database is configured (`using_database()`) — see `_rate_limit_last()` / `_rate_limit_set()`. The 5-minute window now survives restarts and is shared across horizontally-scaled instances. Falls back to the original in-memory dict only in local/Studio mode where no database is configured. **Redis was not introduced** — it isn't installed or provisioned; the KV store already provides durable, shared storage, so the TRD §5.6 "move to Redis" note is superseded by this simpler fix. Tested in `tests/test_skillpack_sync.py`.

---

## SG-05 — Tracking HMAC Secret Is Optional

**Severity:** Medium  
**Component:** Cloud API — `app/api/tracking_routes.py:_verify_token()` (line 47)

### Description

`_verify_token(company, token)` first checks whether a per-company tracking token exists in `kv_store`. If it exists and matches, the call is accepted. If no token is stored **and** `SKILL_TRACKING_HMAC_SECRET` is not set in config, the function returns `{"workspace_id": ""}` — effectively accepting the request as a local/dev call with an empty workspace.

In production, every published pack gets a tracking token via `_tracking_token()` in `publish_routes.py`. But if the KV store loses a token (e.g. a failed migration or explicit deletion), the fallback silently accepts all telemetry from anyone who POSTs to that company's endpoint.

### Current Mitigation

Accepted telemetry is write-only from an attacker's perspective — it can inflate run counts but cannot read data. The `SKILL_TRACKING_HMAC_SECRET` env var is documented in `ROUTER_SETUP.md` as required for production.

### Recommended Fix

In production (`SKILL_AUTH_REQUIRED=true`), change the fallback to **reject** when `tracking_tokens[company]` is absent (return 401, not a synthetic workspace dict). The HMAC secret path can remain for legacy scenarios but should log a warning when used.

### Fix Applied

`_verify_token()` now returns `None` (→ 401) instead of the synthetic `{"workspace_id": ""}` whenever either `SKILL_TRACKING_HMAC_SECRET` or `SKILL_AUTH_REQUIRED` is set, and logs a `logger.warning(...)` every time a company with no stored token is rejected. The permissive fallback only survives in true local dev (`auth_required=False` and no HMAC secret configured). `_validate_production_config()` in `app/main.py` now also requires `SKILL_TRACKING_HMAC_SECRET` when `SKILL_AUTH_REQUIRED=true`, so production can no longer boot without it. Tested in `tests/test_llm_proxy_and_publish.py`.

---

## SG-06 — Telemetry Payload Unbounded

**Severity:** Medium  
**Component:** Cloud API — `app/api/tracking_routes.py:ingest_events()` (line 647)

### Description

The ingest endpoint accepts `body.get("evts", [])` and appends it verbatim to the `tracking/{company}` KV row via `db_append`. There is no cap on the length of the `evts` array, no limit on the size of individual event fields, and no check that field names match the documented compact schema (`e`, `ts`, `si`, `tier`, etc.).

An attacker with a valid tracking token (embedded in a leaked installer) can POST arbitrarily large payloads to inflate KV storage and dashboard query times.

The 1MB general body cap in `ProductionRequestMiddleware` provides some protection, but a legitimate-looking payload can still contain many events approaching 1MB.

### Recommended Fix

- Cap `evts` array at a reasonable maximum (e.g. 200 events per batch).
- Truncate or reject individual field values that exceed a sane length (e.g. 256 chars per field).
- Consider a per-company daily ingest quota (track in KV alongside the telemetry).

### Fix Applied

`ingest_events()` now caps each batch to `SKILL_TRACKING_MAX_EVENTS_PER_BATCH` (default 200, oldest-first truncation) and truncates any string field longer than `SKILL_TRACKING_MAX_FIELD_CHARS` (default 256) before appending to KV storage. A `logger.warning(...)` fires once per request whenever truncation occurred, including the company and before/after counts. A per-company daily quota was **not** added — the batch/field caps bound worst-case storage per request, which was the concrete risk; a daily quota is tracked as a possible follow-up, not required to close this gap. Tested in `tests/test_llm_proxy_and_publish.py`.

---

## SG-07 — Installer Download Is Fully Public

**Severity:** Medium  
**Component:** Distribution — `app/api/publish_routes.py:get_installer()` (line 509)

### Description

`GET /api/v1/installers/{slug}` streams the installer `.exe` to anyone who knows the slug. Slugs are validated with `_validate_slug()` (alphanumeric + `-_`, max 64 chars) but not treated as secrets. They appear in dashboard URLs, in `pack.json` embedded in every installer, and potentially in marketing materials.

The installer bundles: the runtime exe, Chromium, the compiled skill pack (data-only), and `pack.json` (which contains the sync token and tracking token). Downloading the installer is therefore equivalent to obtaining the sync token.

### Current Mitigation

Skills packs contain no credentials. Session encryption uses a separate per-machine key, so obtaining the installer cannot decrypt any user's browser session. The slug is required to be known to the downloader.

### Recommended Fix

Short-term: generate a per-download signed URL (time-limited, signed with `SKILL_INSTALLER_SIGNING_KEY`) and serve the binary via redirect, so the stable slug URL becomes a meta-endpoint rather than a direct download. This removes the ability to share a permanent download link.  
Long-term: require the end user to be authenticated with the company's identity provider before receiving the installer (delivered as a first-party install flow, not a public link).

### Fix Applied

`get_installer()` and `get_installer_version()` now require `ts`+`sig` query params whenever `SKILL_INSTALLER_SIGNING_KEY` is configured — `sig` is `HMAC-SHA256(key, f"{ts}:{slug}:{version or ''}")`, checked with `secrets.compare_digest` and a max age of `SKILL_INSTALLER_SIGNING_WINDOW` (default 600s). This mirrors the timestamped-HMAC pattern already used for the SG-02 proxy-identity fix (`app/services/saas.py:_trusted_proxy_identity()`), rather than the Ed25519 scheme used for manifest signing — signing and verification both happen on the same backend here, so symmetric HMAC is simpler and sufficient. The authenticated, `require_admin`-gated `get_installer_versions()` endpoint mints fresh signed `download_url` values on every call, so the dashboard always hands out valid links. **The `ts`+`sig` requirement is skipped entirely when `SKILL_INSTALLER_SIGNING_KEY` is unset**, preserving the previous public-download behavior for local dev — `_validate_production_config()` now requires the key when `SKILL_AUTH_REQUIRED=true`, so production can't boot without it. The dashboard's `SkillPackageVersionsPage.tsx` no longer constructs an unsigned fallback URL client-side (impossible without shipping the secret to the browser) — its legacy single-row fallback for pre-versioning skill packages now disables the download button instead. Tested in `tests/test_llm_proxy_and_publish.py`.

---

## SG-08 — Sync Token Is a Shared Installer Secret

**Severity:** Low  
**Component:** Distribution — `app/api/skillpack_update_routes.py:_verify_sync_token()` (line 53), `runtime/sync.js`

### Description

The sync token (`secrets.token_urlsafe(32)`) is minted at first publish and reused across all subsequent publishes and all installer copies for that company. Every end user who installs the skill package has the same token embedded in their `pack.json`. The token grants read access to the company's current skill pack delta endpoint.

Because the token is in `pack.json` inside every installer binary, it is effectively a publicly-distributable credential for anyone with access to the installer file (see SG-07).

### Current Mitigation

- The token grants **read-only** access to data-only skill pack files (selectors, intents, recovery strategies — no credentials).
- Session encryption (`AES-256-GCM`, per-machine HKDF key stored in OS keychain) is entirely separate from the sync token, so a leaked installer cannot decrypt any user's browser session.
- The token can be rotated by deleting the `sync_tokens[slug]` KV entry, which forces a new token on the next publish.

### Recommended Fix

Issue per-install tokens at installer-download time (requires solving SG-07 first), or move to short-lived tokens derived from the long-lived root token (runtime exchanges the root for a short-lived one at startup, mitigating the impact of leaked installers).

**Note (2026-09-02):** the new recovery-artifact endpoint (`POST .../skill-packs/{slug}/artifacts`, added for the content-addressed artifact store — `app/api/skillpack_update_routes.py:get_skill_artifacts()`) authenticates with this same sync token and is deliberately **not** rate-limited like the delta endpoint beside it (the artifact pass must run immediately after code lands, and the delta route's 5-minutes-per-token limit would block every call). Same blast radius as this gap — read-only, single-company skill data — but a leaked token can now also be used to repeatedly rebuild the zip response for bandwidth/cost, not just poll the delta. No fix planned; tracked here rather than as a new SG since the underlying exposure (a leaked installer token) is identical.

---

## SG-09 — No Code Signing on Self-Update Binary

**Severity:** High — ⚠️ Partially Fixed 2026-07-01 (manifest signing done; binary Authenticode signing still open)  
**Component:** Runtime — `runtime/manifest_manager.js`, `.github/workflows/build-runtime-host.yml`

### Description (original)

The runtime self-update mechanism downloaded `runtime-win.exe` from a URL supplied in an unsigned manifest (`GET /api/v1/updates/runtime-manifest`), verifying only a SHA-256 hash. Three problems: (1) the manifest URL/content wasn't code-signed by Conxa, (2) the downloaded binary itself wasn't Authenticode-signed, (3) if the manifest endpoint or CDN delivery were compromised, an attacker could replace both the manifest hash and the binary — the SHA-256 check would pass because the hash came from the same compromised source. The old `.bat`-based apply mechanism (`runtime.exe.next`) has since been removed entirely (see SG-10).

### Fix Applied (manifest half)

The Enterprise-Grade Auto-Update Architecture (2026-07-01) replaced the unsigned manifest with a single Ed25519-signed `GET /api/v1/manifest.json`. `runtime/manifest_manager.js:verifyManifestSignature()` verifies the signature against a public key baked into the host exe at build time (`global.__manifestPublicKey`, stamped from `package.json`) — the trust anchor lives in the already-installed binary, not fetched from the network, exactly as the original recommendation asked. A manifest that fails verification is discarded outright and treated identically to a network failure (falls back to the last previously-verified cache). `updateHostComponent()` additionally spawns the freshly-downloaded exe with `--selfcheck` before `current` is ever pointed at it.

### Still Open (binary half)

**Hardening since (2026-08-04):** `app/main.py::_validate_production_config()` now refuses to boot the backend when `SKILL_AUTH_REQUIRED=true` and `CONXA_MANIFEST_SIGNING_KEY` is unset. Without the key, `manifest_signer.load_signing_key()` returns `None` and `/api/v1/manifest.json` is served **unsigned** — which every runtime then discards as unverifiable, silently halting self-updates platform-wide with no error surfaced on either side. Failing the boot converts that into a loud misconfiguration.

The downloaded `conxa-runtime.exe` itself is still **not Authenticode-signed** — only its SHA-256 (sourced from the now-signed manifest) is checked. If the manifest signing key (`CONXA_MANIFEST_SIGNING_KEY`, server-side only) were ever compromised, an attacker could still ship an arbitrary signed manifest entry pointing at a malicious binary. `build-runtime-host.yml` has no `signtool` step for `conxa-runtime.exe` (unlike the Studio Electron installer — see Sales-Blockers.md 2.5, which has inert `electron-builder.yml` signing scaffolding for the *Studio* installer, not this binary).

### Recommended Fix (remaining)

Sign `conxa-runtime.exe` with a Conxa Authenticode certificate as part of `build-runtime-host.yml`, and verify the signature in `manifest_manager.js` before `_selfcheck`/activation — a second, independent trust check beyond the manifest's own signature, so a compromised manifest-signing key alone is insufficient to install a malicious binary.

---

## SG-10 — Update `.bat` Uses `Math.random()` for Temp Filename

**Severity:** Low — ✅ Resolved 2026-07-01 (mechanism removed, not patched)  
**Component:** Runtime — formerly `runtime/server.js:_applyPendingUpdate()`

### Description (original)

The old single-backup update script wrote a `.bat` file to `os.tmpdir()` with a suffix derived from `Math.random().toString(36).slice(2)` — not a cryptographically secure PRNG. An adversary who could create files in `%TEMP%` could in theory pre-create `conxa-update-<predicted-suffix>.bat` files.

### Resolution

The Enterprise-Grade Auto-Update Architecture (2026-07-01) replaced the entire `.bak`/`.next`/`.bat` single-backup update dance with the versioned-directory model (`runtime/version_manager.js`): each component update downloads into its own `<component>/<version>/` directory and flips a directory junction atomically — there is no `.bat` file, no `os.tmpdir()` staging, and no `Math.random()` call in the update path anymore. `runtime/manifest_manager.js:updateAppComponent()` does use a predictable-looking staging name (`` `${versionDir}.staging-${process.pid}-${Date.now()}` ``) for the app-layer zip extraction, but this lives under `CONXA_DIR` (not the world-writable `%TEMP%`) and is immediately `fs.renameSync`'d over the real version directory — the original attack (planting a `%TEMP%` file with a predicted name ahead of time) no longer applies since there's no `%TEMP%` write in the path. No further action needed.

---

## SG-11 — Plaintext Session Fallback Is Silent

**Severity:** Medium  
**Component:** Runtime — `runtime/auth_manager.js:saveRawSession()` (line 99)

### Description

When `_getKeytar()` fails to load the native `keytar.node` module (e.g. ABI mismatch immediately after a self-update that swaps `keytar.node`), the module falls back to a JSON file (`~/.conxa/cache/.keytar.json`) instead. More critically, within the runtime session flow, a keytar failure in `getSessionKey()` silently causes `saveEncryptedSession()` to throw, and the caller (`auth_manager.refreshSession()`) then calls `saveRawSession()` — writing the full Playwright `storageState` (cookies, localStorage, session tokens for the **target platform**) as plaintext to `{company}_raw_state.json`.

The end user sees no warning. The file mode is `0o600`, which on Windows is not meaningfully enforced.

This window used to be most dangerous during the keytar ABI swap in the old `_applyPendingUpdate()` flow: between moving `keytar.node.next` and the next process restart, `keytar.node` was temporarily the new ABI while the running process still had the old ABI loaded. That specific window is gone — the versioned-directory model (SG-10) ships `keytar.node` inside `conxa-runtime/<version>/` and never swaps a file underneath a running process. An ABI mismatch can still occur for other reasons (a hand-copied module, a partially-restored install), so the fallback behaviour below still matters.

### Current Mitigation

The raw session file is scoped to the user's local machine at `%APPDATA%\Conxa\cache\sessions\`. An attacker needs local filesystem access to read it.

### Recommended Fix

- In `saveEncryptedSession`, if encryption fails, log a warning and return without falling back to plaintext.
- At startup in `auth_manager.js`, after successfully loading keytar, check for any existing `{company}_raw_state.json` files and re-encrypt them, then delete the plaintext originals.
- Emit a visible log event (`"warn"` level) whenever a plaintext session file is written or read.

### Fix Applied

Investigation found the actual code was worse than described: `server.js`'s post-execution save (`saveRawSession()` after every successful skill run) was **unconditional**, with no encryption attempt at all — not merely a rare fallback. Fixed at the root:

- `saveEncryptedSession()` now returns `true`/`false` instead of silently swallowing every error, and logs `"warn" session_encryption_failed` on failure via an injected `logFn` (dependency-injected the same way `sessionsDir` already was, to avoid a circular import with `server.js`'s logger).
- All three write sites — `server.js`'s post-execution save, and `browser.js`'s initial interactive-auth capture and mid-execution `captureReAuth()` — now attempt `saveEncryptedSession()` first and only call `saveRawSession()` when it reports failure, which itself now logs `"warn" plaintext_session_written`.
- `_getKeytar()` logs `"warn" keytar_unavailable_fallback` when it falls back to the plaintext keytar shim; `loadRawSession()` logs `"warn" plaintext_session_loaded` whenever a plaintext file is actually read.
- New `reencryptPlaintextSessions()` sweeps `SESSIONS_DIR` for stale `*_raw_state.json` files at every startup (wired into `server.js`'s `startupSync`), re-encrypting and deleting each on success; a file is left in place (with a warning) if re-encryption fails, so no data is lost.

No new config — reuses the existing `CONXA_DATA_DIR`/`SESSIONS_DIR` layout and `server.js`'s structured `log()`. Tested in `runtime/test/test_auth_recovery.js`.

---

## SG-12 — Company Name Used in File Paths Without Re-Validation

**Severity:** Low  
**Component:** Runtime — `runtime/auth_manager.js` (line 77, 99), `runtime/sync.js`

### Description

The runtime reads `company` from the `pack.json` files in `SKILL_PACKS_DIR` and uses it directly in `path.join(sessionsDir, `${company}_state.json`)`. The value is not re-validated against the slug allowlist (`[a-zA-Z0-9-_]`) before use.

Company slugs are validated server-side by `_validate_slug()` when published, and the `pack.json` files in `SKILL_PACKS_DIR` are written by either the installer or `sync.js` (which verifies SHA-256 integrity). However, if a user manually edits `pack.json` and sets `company` to a path-traversal value (e.g. `../../etc/passwd`), `path.join` on Node.js would resolve it relative to the session directory.

On Windows, `\` in the company field would also traverse directories.

### Current Mitigation

`path.join` normalises `..` components but does not restrict the result to stay within `sessionsDir`. Exploitation requires local file access to modify `pack.json`, which already implies significant local access.

### Recommended Fix

Add a slug validation function in `sync.js` / `auth_manager.js` and sanitise the `company` field when reading from disk:
```js
function isValidSlug(s) { return /^[a-zA-Z0-9_-]{1,64}$/.test(s); }
```
Reject packs with invalid company slugs during sync load.

---

## SG-13 — No Per-User Identity at Runtime

**Severity:** Low  
**Component:** Runtime — `runtime/server.js`, `runtime/tracker.js`

### Description

The runtime authenticates to Conxa Cloud per-company only (via the sync token in `pack.json`). There is no per-user Conxa identity on the end-user machine. Telemetry events include a `uid` field populated from `INSTALL_ID` — a random UUID generated on first run and stored in `~/.conxa/data/install_identity.json`. This UUID:

1. Is locally generated with no cryptographic binding to any user identity or machine hardware.
2. Can be copied or spoofed by anyone with filesystem access.
3. Has no association with the target-platform identity (the credentials used to log into the automated web application).

This means telemetry run counts are advisory and can be spoofed, and there is no way to distinguish between runs from two different human users on the same machine vs. the same user across reinstalls.

### Recommended Fix

This is acceptable for the current product stage where Conxa is a per-company distribution model. Document that `uid` is an installation identifier, not a user identifier, and do not surface it as a "user" metric in the dashboard.

---

## SG-14 — BYOK Key-at-Rest

**Severity:** Medium  
**Component:** Cloud API — `app/services/byok.py`

### Description

Enterprise workspaces may configure their own Azure OpenAI deployment (`docs/PRD.md` §11, `docs/TRD.md`
§13.5). The API key is AES-256-GCM encrypted before storage in the `workspace_llm_keys` KV namespace,
under a single symmetric key read from `SKILL_BYOK_ENCRYPTION_KEY`. Consequences of that design:

1. Every workspace's BYOK secret is encrypted under the **same** key — a leak of
   `SKILL_BYOK_ENCRYPTION_KEY` decrypts every stored Azure key at once, not just one workspace's.
2. There is no key rotation mechanism. Rotating `SKILL_BYOK_ENCRYPTION_KEY` would need to
   decrypt-then-re-encrypt every stored record with the old key still available — not automated today.
3. The unconfigured-key failure mode is safe by design (`byok_not_configured`, refuses to
   encrypt/decrypt rather than falling back to plaintext), but that only protects against
   misconfiguration, not against the key itself leaking (e.g. via the deploy environment).

### Recommended Fix

Acceptable at current BYOK adoption (a handful of Enterprise workspaces). Move to a per-workspace data
key wrapped by a managed KMS (envelope encryption) before BYOK adoption scales — that bounds a single
key leak to one workspace and makes rotation tractable.

---

## SG-15 — Machine-Fingerprint Handling

**Severity:** Low  
**Component:** Cloud API — `app/api/machine_binding.py`, `app/services/entitlements.py`

### Description

Machine binding (`docs/TRD.md` §13.6) sends `X-Conxa-Machine`, a SHA-256 hash of the Windows
`MachineGuid`, on every build-side cloud call. The raw GUID never leaves the machine — only the hash is
transmitted and stored (`workspace_devices` KV). That said, a SHA-256 hash of a stable, low-entropy
identifier (a per-Windows-install GUID) is itself a **stable cross-session identifier**: it does not
protect against re-identifying the same machine over time, only against recovering the original GUID
value. This is the intended behavior — machine binding's entire purpose is stable re-identification —
but it means the hash should be treated as PII-adjacent (a device fingerprint), not as an anonymous
token, for any future data-handling or retention policy.

### Recommended Fix

No fix needed for current use (workspace-scoped device counting). If machine hashes are ever
aggregated or exposed cross-workspace, treat them with the same care as any other device fingerprint.

---

## SG-16 — Analytics Retention As Data Minimisation

**Severity:** Low  
**Component:** Cloud API — `app/services/tracking.py`

### Description

Per-plan analytics retention (`docs/TRD.md` §13.5 — Free 0 days, Starter 90, Pro 365, Enterprise
custom) is enforced by filtering `_visible_run_records`/`_visible_runtime_registrations` **on read**.
There is no write-side prune: telemetry older than a workspace's retention window still exists in
storage, it's just no longer returned by the query paths a workspace's own dashboard uses. This is
correctness-complete for the customer-facing promise ("you can't see data older than N days") but not
a real deletion guarantee — the raw event rows remain in `tracking/{company}` KV until something else
removes them.

### Recommended Fix

Add an amortised prune job (on ingest or a periodic sweep) that deletes rows past the longest
contractually-possible retention window. Tracked in `TODO.md` — deferred in the 2026-08-08
restructuring because it required either a workspace-id-keyed billing read that doesn't cleanly fit the
existing `Principal`-based `billing_for()` API, or accepting a correctness compromise; read-side
filtering was judged sufficient for the customer-facing guarantee at current scale.

---

## SG-17 — Starter External Distribution Is Detectable, Not Blocked

**Severity:** Low (by design)  
**Component:** Distribution — `app/api/publish_routes.py`

### Description

Free and Starter workspaces are gated to `distribution="internal"` installers; Pro and Enterprise may
distribute externally (`docs/TRD.md` §13.5, `ensure_distribution_allowed`). That gate is enforced on
what the Studio *declares* via the `distribution` query param at installer upload — a Starter customer
who deliberately uploads with `distribution=external` omitted, then hands the resulting "internal"
installer to their own external customers anyway, is not technically prevented from doing so. Conxa has
no way to inspect what a downloaded `.exe` is later used for.

### Recommended Fix

This is a deliberate trade-off, not an oversight (see `docs/TRD.md` §13.6): hard-blocking based on
runtime install behavior would mean building DRM into the installer, which the product doesn't want.
The boundary that's actually enforced is what the installer is *stamped and branded* as (internal vs.
external, Conxa vs. white-label) — visible in the dashboard and telemetry, and a contract violation a
Starter customer can be caught committing, not one that's cryptographically prevented. No fix planned;
document the limit explicitly wherever Starter's distribution terms are described so it doesn't read as
an oversight later.

---

## SG-18 — Seat Limit Not Enforced

**Severity:** Low — ✅ Fixed 2026-08-22 (different mechanism than originally recommended — see below)  
**Component:** Billing — `app/services/entitlements.py`, `app/api/deps.py`, team invites

### Description

`PLAN_LIMITS[plan]["seats"]` (1/3/10 for free/starter/pro) is never enforced. Unlike machines
(`ensure_machine_slot`, which blocks a *new* machine once the workspace is at its plan's cap while
grandfathering already-registered ones) and every LLM/compile/distribution entitlement (all re-derived
live from current billing on every check — see `ensure_ops_tier`, `ensure_distribution_allowed`,
`reserve_compile_credit`), there is no equivalent gate for seats. Team invites bypass the backend
entirely: they go straight from the dashboard's Clerk UI component to Clerk's own API, so the backend
never observes an invite happening. `membership_count_for` only feeds a read-only usage meter on the
dashboard — a workspace can invite unlimited members on any plan today, and downgrading a plan doesn't
remove anyone either. See `TODO.md` CLOUD-9.

### Recommended Fix

Add a Clerk webhook (`organizationMembership.created`) that checks the new member count against the
workspace's plan limit and revokes the membership via Clerk's API if over — this is the only
interception point available, since invites never pass through our own API. Not yet built; tracked as
`TODO.md` CLOUD-9 (Complexity M).

### Fix Shipped (2026-08-22)

No Clerk webhook was built — the invite itself still succeeds in Clerk, unchanged, since intercepting
it would require exactly the webhook infrastructure above. Instead, enforcement moved to the point
Conxa's backend actually observes a new member: their first authenticated request. `current_principal`
(`app/api/deps.py`) now checks `saas.is_known_member(user_id, workspace_id)` before calling
`ensure_principal` (which would otherwise silently create the membership row); for a not-yet-known
pair it calls the new `entitlements.ensure_seats_available(principal)`, which compares the workspace's
live Clerk member count (`_clerk_org_member_count`) against the plan's `seats` limit and raises
`seat_limit_exceeded` (402) if the workspace is already at or over cap. Practical effect: an
over-the-cap member can still be invited in Clerk, but every request they make to Conxa fails until a
seat opens up or the plan is upgraded — the seat is unusable, not merely uncounted. Existing members
are never re-checked or removed by a later downgrade (soft-lock, matching the machine/workflow gates).
This closes the practical exposure (unlimited free team usage) without new webhook infrastructure; the
webhook remains the only way to stop the invite from succeeding in Clerk at all, so re-open `TODO.md`
CLOUD-9 if that stronger guarantee is ever required.

---

## SG-19 — One Signing Key Now Covers Two Artifacts, No Rotation

**Severity:** Low  
**Component:** Cloud API / Runtime — `app/api/manifest_signer.py`, `runtime/app/manifest_manager.js`, `runtime/app/policy_gate.js`

### Description

PROD-18's governance policy document (`GET`/`PUT /api/v1/tracking/{workspace_id}/policy`,
`app.services.tracking.write_governance_policy`) is signed with the same Ed25519 keypair
that already signs the runtime self-update manifest, verified with the same
`verifyManifestSignature` on the runtime side. This was a deliberate reuse — it adds no
new trust anchor and no new crypto code — but it does mean a single keypair, stored only
as the `CONXA_MANIFEST_SIGNING_KEY` env var with no key ID and no rotation mechanism (this
was already true of the manifest alone before this change; see the "no code signing" gap
in TRD §17 for the original context), now backs two independent artifact types. Rotating
the key requires coordinating both consumers at once, and there is no way to distinguish
"signed with the current key" from "signed with a retired one" on either side.

The signed policy document does carry a `key_id` field (`"conxa-manifest-1"` today), but
nothing reads it — it exists purely so a future rotation is a data change instead of a
wire-format break.

### Recommended Fix

Not built. When rotation is actually needed: add key-ID-aware verification on the runtime
side (try the current key, fall back to a short-lived set of recently-retired keys keyed
by `key_id`) and a rotation window on the cloud side (sign new artifacts with the new key,
keep the old key valid for verification only until every deployed runtime has picked up
the new public key via a runtime update). Tracked in `TODO.md`.

---

## SG-20 — Popup-Shape Telemetry (`app_obs`) Leaves The Customer Machine

**Severity:** Low (mitigated)
**Component:** Runtime / Cloud API — `runtime/app/scrub.js`, `runtime/app/app_knowledge.js`, `app/api/app_graph_routes.py`

### Description

When a run meets a decision popup it cannot place, the runtime emits an `app_obs` telemetry event
(`docs/Backend-Schema.md` telemetry table). Until now no event carried page-derived text off the machine
(the only exception, `drift_detected.url`, is a known wider field). `app_obs` carries the page **host and
route**, up to eight **title words** and up to six **button labels**.

Mitigations in place: everything goes through `scrub.js` (URLs, emails, quoted spans, file-like words and
any word with a digit removed; a label that needed any scrubbing is dropped whole; route segments with a
digit or 16+ characters become `:id`; the popup's message is never sent), which is tested against the same
cases as the Studio-side scrubber so the two cannot drift; events are only sent when telemetry is already
enabled for the pack; the cloud re-bounds every field on read; data is workspace-scoped, behind the same
plan gate and retention as the drift queue; nothing is aggregated across workspaces.

### Residual risk

The scrubber is heuristic: a button label that is itself sensitive text with no digit, quote, dot or `@`
(for example a person's first name used as a button) would pass. Titles and labels of *decision popups* are
overwhelmingly application chrome, which is why only decision popups are reported.

### Recommended Fix

If a customer needs it, add a pack-level switch that disables `app_obs` while leaving other telemetry on.
Cross-workspace sharing of popup shapes (TODO APP-1, phase 8) must not be enabled before this entry is
re-reviewed.

---

## Cross-Reference: TRD §17 (Known Gaps & Tech Debt)

The following gaps from `TRD.md §17` overlap with security concerns and are tracked there but not duplicated here:

| TRD §17 entry | Security relevance |
|---|---|
| No enterprise RBAC enforcement | Covered by SG-01 above — ✅ Fixed |
| Sync token is a shared installer secret | Covered by SG-08 above |
| Rate limit cache in-memory | Covered by SG-04 above — ✅ Fixed |
| Installer download fully public | Covered by SG-07 above |
| No device/runtime registration | Resolved — `runtime_registrations` KV + `GET /api/v1/telemetry/runtimes` (TRD §3.2, Sales-Blockers.md 2.1); residual per-user gap is SG-13 |
| `SKILL_TRACKING_HMAC_SECRET` optional | Covered by SG-05 above |
