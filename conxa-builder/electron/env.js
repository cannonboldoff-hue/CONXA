"use strict";
/**
 * Build Studio environment resolver — the dev/prod switch for the desktop app.
 *
 * Returns the CONXA_* config injected into the spawned Python backend so the two
 * lanes stay isolated: dev points at the local/dev cloud + dev Clerk and keeps its
 * state under ~/.conxa-build-studio-dev; prod points at the live cloud.
 *
 * SAFETY DEFAULT = prod. The Build Studio is distributed to SaaS companies who
 * publish to the production cloud, so an unconfigured install must behave exactly
 * as it does today (apis.conxa.in / clerk.conxa.in / ~/.conxa-build-studio). Dev is
 * opt-in for Conxa's own developers via an explicit CONXA_ENV=dev (set by the
 * dev/prod launcher). Every value still honors an explicit env override.
 */
const path = require("path");
const os   = require("os");

function isExplicitDev() {
  const raw = String(process.env.CONXA_ENV || "").trim().toLowerCase();
  return raw === "dev" || raw === "development" || raw === "local";
}

/** Resolve the CONXA_* block for the active environment. */
function resolve() {
  const dev = isExplicitDev();
  const home = os.homedir();
  return {
    env: dev ? "dev" : "prod",
    dev,
    // Cloud API base for Studio → cloud (publish, proxy, entitlements).
    cloudApi: process.env.CONXA_CLOUD_API || (dev ? "http://127.0.0.1:8000" : "https://apis.conxa.in"),
    // Public website (Cloud frontend). The guided tour's practice site lives here; hints are injected only on this host.
    webOrigin: process.env.CONXA_WEB_ORIGIN || (dev ? "http://localhost:3000" : "https://www.conxa.in"),
    // Public base embedded into pack.json at publish time — must match the cloud
    // this Studio publishes to, so dev-built installers never phone home to prod.
    apiBaseUrl: process.env.SKILL_API_BASE_URL || (dev ? "http://127.0.0.1:8000" : "https://apis.conxa.in"),
    // Clerk OAuth (override CONXA_CLERK_DOMAIN/_CLIENT_ID for a separate dev instance).
    clerkDomain: process.env.CONXA_CLERK_DOMAIN || "https://clerk.conxa.in",
    clerkClientId: process.env.CONXA_CLERK_CLIENT_ID || "Z7O8UdIVowd3Aegx",
    // Studio state root — isolates deps cache, sandbox, and generated bundles per lane.
    studioHome: process.env.CONXA_STUDIO_HOME || path.join(home, dev ? ".conxa-build-studio-dev" : ".conxa-build-studio"),
    updateChannel: String(process.env.CONXA_UPDATE_CHANNEL || (dev ? "dev" : "stable")).trim().toLowerCase(),
  };
}

/**
 * Build the env block to merge into the spawned Python backend's environment.
 * Only defaults that are not already explicitly set by the shell are applied,
 * so a developer can still override any single value.
 */
function backendEnv() {
  const r = resolve();
  return {
    CONXA_ENV: r.env,
    CONXA_CLOUD_API: r.cloudApi,
    SKILL_API_BASE_URL: r.apiBaseUrl,
    CONXA_CLERK_DOMAIN: r.clerkDomain,
    CONXA_CLERK_CLIENT_ID: r.clerkClientId,
    CONXA_STUDIO_HOME: r.studioHome,
    CONXA_UPDATE_CHANNEL: r.updateChannel,
  };
}

module.exports = { resolve, backendEnv, isExplicitDev };
