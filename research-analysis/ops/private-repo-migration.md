# Private GitHub Repo Migration

## Short Answer

The source repository can be made private, but release artifact downloads must stay public or be served by Conxa Cloud. The current app has unauthenticated clients that fetch runtime and Studio binaries from URLs published in update manifests. Those clients cannot use private GitHub credentials.

The main migration is not a code visibility problem. It is an artifact hosting and deployment-permission problem.

## Current Repo Touchpoints

- Source remote: `https://github.com/cannonboldoff-hue/CONXA.git`.
- Release build workflows (runtime is now a two-layer host/app split, not a single workflow):
  - `.github/workflows/build-runtime-host.yml` — builds the host exe (`conxa-runtime.exe` + `keytar.node`), tags `host-vX.Y.Z`, publishes to a GitHub Release. `permissions: contents: write`.
  - `.github/workflows/build-runtime-app.yml` — builds the obfuscated app-layer bundle (`conxa-app-*.zip`), tags `app-vX.Y.Z`, publishes to a GitHub Release. `permissions: contents: write`.
  - `.github/workflows/build-studio.yml` — builds the Build Studio Electron installer via `electron-builder publish`, using `GH_TOKEN` to publish directly to a GitHub Release.
  - `.github/workflows/promote-release.yml` — dev→stable promotion. Downloads the byte-verified dev artifact **from its GitHub Release URL**, re-uploads it under the clean stable tag (`gh release create`/`upload`), and POSTs the resulting manifest record (which embeds a fresh `https://github.com/$GITHUB_REPOSITORY/releases/download/$TARGET_VERSION/...` URL, built inline in the workflow) to the cloud's admin API. This is the **only** path by which prod receives a release, and it is hard-wired to GitHub Releases, not just defaulted via env var.
- Default release URLs live in:
  - `.env.example`, `.env.prod.example`
  - `conxa-cloud/backend/app/api/updates_routes.py` (all default URLs derive from `_GITHUB_REPO` = `CONXA_GITHUB_REPO` env var, default `cannonboldoff-hue/CONXA`, via `_release_url()`)
- Runtime and Studio artifacts currently default to GitHub Release download URLs:
  - `CONXA_RUNTIME_CDN_URL` (`.env.prod.example`/`.env.example` — legacy/base override)
  - `CONXA_STUDIO_WIN_URL`, `CONXA_STUDIO_LATEST_YML_URL` (Studio installer + electron-updater feed)
  - Host layer (`conxa-runtime.exe`, `keytar.node`) and app layer (`conxa-app-*.zip`) URLs are built from `CONXA_HOST_VERSION` / `CONXA_APP_VERSION` + `_GITHUB_REPO`, not set as standalone URL env vars anymore.
- Public consumers of those URLs:
  - Build Studio `bootstrap.py` fetches `GET /api/v1/updates/deps-manifest` (public, unauthenticated, `manifest_version: 2`) and downloads `nsis.zip`, `conxa-runtime.exe`, `keytar.node`, and `conxa-app-*.zip` from the embedded GitHub Release URLs.
  - The runtime self-updater fetches the **unified, Ed25519-signed** `GET /api/v1/manifest.json?channel=stable` (see `updates_routes.py: unified_manifest` / `_compose_manifest`) and downloads the host/app files listed there — these URLs are the ones `promote-release.yml` bakes in as GitHub Release links. The older `GET /api/v1/updates/runtime-manifest` / `runtime-app-manifest` endpoints still exist but are explicitly marked **deprecated in favour of `/manifest.json`**.
  - The Cloud frontend fetches `GET /api/v1/updates/studio-manifest` to show the Build Studio installer download link, and `GET /api/v1/updates/studio/latest.yml` proxies electron-updater's differential-update feed — which, when `CONXA_STUDIO_LATEST_YML_URL` is unset, also falls back to a GitHub Release URL.

## Required Changes Before Making The Repo Private

1. Reauthorize Render GitHub access.
   - Confirm the Render GitHub App has access to the now-private repo.
   - Confirm the `conxa-api` service still points at the intended branch/root directory.
   - If deploys stop, update the Git credentials in the Render service settings.

2. Reauthorize Vercel GitHub access.
   - Confirm the Vercel GitHub integration can access the private repo.
   - For private org repos, ensure commit authors also have access to the Vercel project/team, otherwise deployments can be blocked.

3. Check GitHub Actions billing and storage.
   - Private repositories use the account or organization Actions minutes and artifact/cache storage quota.
   - The Windows workflows are the important ones here because runtime and Studio builds run on `windows-latest`.
   - Keep the workflows if desired, but treat GitHub Releases as build output or internal release records, not as the public customer CDN.

4. Move public artifact URLs away from the private repo.
   - Set these Render environment variables to public, unauthenticated URLs:
     - `CONXA_STUDIO_WIN_URL`, `CONXA_STUDIO_LATEST_YML_URL`
     - `CONXA_RUNTIME_CDN_URL` if still referenced by any release path
   - The host/app layer URLs (`conxa-runtime.exe`, `keytar.node`, `conxa-app-*.zip`) are no longer individually overridable env vars — they're derived inside `updates_routes.py`'s `_release_url()` from `_GITHUB_REPO`. Point `CONXA_GITHUB_REPO` at nothing meaningful once private, or (better) change `_release_url()` to build from a Conxa-owned artifact base instead of GitHub.
   - `promote-release.yml` hard-codes `base="https://github.com/$GITHUB_REPOSITORY/releases/download/$TARGET_VERSION"` when it POSTs the stable manifest record — this must change to a Conxa-owned public base too, or the signed `manifest.json` the runtime self-updater trusts will keep pointing at private-repo URLs even after step 4a above.
   - Recommended host: Conxa-owned object storage/CDN or a Conxa Cloud file endpoint.
   - Acceptable short-term fallback: a separate public release-only repo containing only binaries and checksums.

5. Do not embed GitHub credentials in shipped software.
   - Do not put GitHub PATs in Build Studio, the runtime, installers, frontend code, manifests, or query strings.
   - Do not make customer machines authenticate to GitHub just to update Conxa runtime files.
   - If private GitHub assets must remain the build source, copy them server-side into public Conxa-hosted artifact URLs before publishing manifests.

## What Probably Does Not Need To Change

- Local developer setup scripts use repo-local paths and public package registries, not public GitHub source downloads.
- The local git remote URL can remain `https://github.com/cannonboldoff-hue/CONXA.git`; developers just need GitHub authentication and repo access.
- The installer download route is separate from GitHub repo visibility. Conxa Cloud already serves installer downloads from its own API surface.
- The supported release path is Build Installer packaging, not a public `npx conxa install` or GitHub-published CLI flow.
- Package manifests and lockfiles mostly reference public dependency metadata; those are not affected by making this source repo private.

## Recommended Target Shape

Use this release flow after the repo becomes private:

1. GitHub Actions (`build-runtime-host.yml`, `build-runtime-app.yml`, `build-studio.yml`) build `conxa-runtime.exe`, `keytar.node`, `conxa-app-*.zip`, and the Build Studio installer from the private repo, as they do today.
2. CI uploads those binaries to Conxa-owned public artifact storage instead of (or in addition to, as an internal record) a GitHub Release.
3. CI computes SHA-256 checksums (already done today).
4. `promote-release.yml` — the only path to the stable channel — is updated so the `base` URL it bakes into the manifest it POSTs to `/api/v1/admin/component-versions/{component}` points at the Conxa-owned public artifact base, not `https://github.com/$GITHUB_REPOSITORY/releases/download/...`.
5. `updates_routes.py`'s `_release_url()` and the `CONXA_STUDIO_WIN_URL` / `CONXA_STUDIO_LATEST_YML_URL` defaults are updated the same way.
6. Build Studio (`deps-manifest`) and runtime clients (`manifest.json`, Ed25519-signed) fetch only Conxa-hosted public URLs — signing/verification logic is unaffected, only the URL host changes.

This keeps source private while preserving unauthenticated customer updates.

## Validation Checklist

- From a logged-out machine, open `GET /api/v1/updates/deps-manifest` and download every URL it returns (`nsis.zip`, `conxa-runtime.exe`, `keytar.node`, `conxa-app-*.zip`).
- From a logged-out machine, open `GET /api/v1/manifest.json?channel=stable` and download every file URL in `conxa_runtime` and `conxa_app`; verify the Ed25519 signature still checks out against the runtime's baked-in public key.
- From a logged-out machine, open `GET /api/v1/updates/studio-manifest` and download `win_url`; also fetch `GET /api/v1/updates/studio/latest.yml` and confirm the rewritten `.exe`/`.blockmap` URLs are reachable.
- Run Build Studio first-run bootstrap from a clean dependency cache and confirm it downloads NSIS, runtime, and keytar successfully.
- Start an installed runtime cold and confirm update check succeeds or safely no-ops.
- Trigger `build-runtime-host.yml`, `build-runtime-app.yml`, and `build-studio.yml` and confirm the dev-channel manifest points at public artifact URLs, not private GitHub Release URLs.
- Run `promote-release.yml` end-to-end and confirm the stable manifest record it posts also points at public artifact URLs (this is the one most likely to be missed, since the URL is constructed inline in the workflow rather than read from an env default).
- Confirm Render deploys after the repo is private.
- Confirm Vercel deploys after the repo is private.

## External References

- GitHub repository visibility effects: <https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility>
- GitHub release asset authentication: <https://docs.github.com/en/rest/releases/assets?apiVersion=2022-11-28>
- Render GitHub repository access: <https://render.com/docs/github>
- Vercel private Git repository rules: <https://vercel.com/docs/git>
- GitHub Actions private repository billing: <https://docs.github.com/en/billing/concepts/product-billing/github-actions>
