"use strict";
/**
 * Clerk OAuth (PKCE) login for Conxa Execute — uses conxa-cloud's OWN Clerk
 * application (clerk.conxa.in), a separate OAuth client from Build Studio's
 * (its own redirect-URI port range so the two don't collide), NOT a second
 * Clerk instance. Node port of conxa-builder/python/services/auth_service.py's
 * flow: local callback server on a fixed port range (pre-registrable
 * redirect_uri), PKCE S256, token exchange + refresh with a 60s leeway.
 * Tokens are stored via safeStorage.encryptString to userData/clerk-session.bin.
 *
 * Requires CONXA_EXECUTE_CLERK_DOMAIN and CONXA_EXECUTE_CLERK_CLIENT_ID to
 * be set to that OAuth client's values — login throws "auth_not_configured"
 * until these are set. Scope includes user:org:read + keeps org_id in the
 * claims: Execute's personal/team context switcher (execute_client.js's
 * getContexts()) needs to know which Clerk organizations this person
 * belongs to, on top of email/name.
 */
const crypto = require("crypto");
const http = require("http");
const fs = require("fs");
const path = require("path");
const { app, shell, safeStorage } = require("electron");

const REFRESH_LEEWAY_S = 60;
const BASE_PORT = Number(process.env.CONXA_EXECUTE_AUTH_PORT || 52841);
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

function clerkDomain() {
  return (process.env.CONXA_EXECUTE_CLERK_DOMAIN || "").replace(/\/+$/, "");
}

function clientId() {
  return process.env.CONXA_EXECUTE_CLERK_CLIENT_ID || "";
}

function sessionPath() {
  return path.join(app.getPath("userData"), "clerk-session.bin");
}

function loadTokens() {
  const p = sessionPath();
  if (!fs.existsSync(p)) return null;
  try {
    if (!safeStorage.isEncryptionAvailable()) return null;
    return JSON.parse(safeStorage.decryptString(fs.readFileSync(p)));
  } catch {
    return null;
  }
}

function saveTokens(tokens) {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("OS encryption is not available; the session was not saved.");
  }
  fs.mkdirSync(path.dirname(sessionPath()), { recursive: true });
  fs.writeFileSync(sessionPath(), safeStorage.encryptString(JSON.stringify(tokens)));
}

function logout() {
  try {
    fs.unlinkSync(sessionPath());
  } catch {
    // already logged out
  }
}

function base64url(buf) {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function pkcePair() {
  const verifier = base64url(crypto.randomBytes(48));
  const challenge = base64url(crypto.createHash("sha256").update(verifier).digest());
  return { verifier, challenge };
}

function findServer(handler) {
  return new Promise((resolve, reject) => {
    let p = BASE_PORT;
    const tryListen = () => {
      const server = http.createServer(handler);
      server.on("error", (err) => {
        if (err.code === "EADDRINUSE" && p < BASE_PORT + 10) {
          p += 1;
          tryListen();
        } else {
          reject(err);
        }
      });
      server.listen(p, "127.0.0.1", () => resolve({ server, port: p }));
    };
    tryListen();
  });
}

function transientError(message) {
  const err = new Error(message);
  err.transient = true; // network/server trouble, not "this session is invalid"
  return err;
}

async function tokenRequest(body) {
  const domain = clerkDomain();
  let resp;
  try {
    resp = await fetch(`${domain}/oauth/token`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": USER_AGENT,
        Accept: "application/json, */*",
        Origin: domain,
        Referer: `${domain}/`,
      },
      body,
    });
  } catch (err) {
    // fetch() rejects on a real network failure (offline, DNS, timeout) —
    // that says nothing about whether the session itself is still good.
    throw transientError(`clerk_token_network_error: ${err.message}`);
  }
  const text = await resp.text();
  let data = null;
  try {
    data = JSON.parse(text);
  } catch {
    // non-JSON error body, handled below
  }
  if (!resp.ok) {
    const desc = data ? data.error_description || data.error : text.slice(0, 300);
    if (resp.status >= 500) throw transientError(`clerk_token_error: ${desc}`);
    throw new Error(`clerk_token_error: ${desc}`);
  }
  const now = Date.now() / 1000;
  return {
    access_token: data.access_token,
    refresh_token: data.refresh_token || "",
    exp: now + Number(data.expires_in || 3600),
  };
}

function exchangeCode(code, verifier, redirectUri) {
  const params = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    client_id: clientId(),
    redirect_uri: redirectUri,
    code_verifier: verifier,
  });
  return tokenRequest(params.toString());
}

function refreshTokens(refreshToken) {
  const params = new URLSearchParams({
    grant_type: "refresh_token",
    refresh_token: refreshToken,
    client_id: clientId(),
  });
  return tokenRequest(params.toString());
}

async function fetchUserinfo(accessToken) {
  const resp = await fetch(`${clerkDomain()}/oauth/userinfo`, {
    headers: { Authorization: `Bearer ${accessToken}`, "User-Agent": USER_AGENT, Accept: "application/json" },
  });
  if (!resp.ok) throw new Error(`userinfo_http_${resp.status}`);
  return resp.json();
}

async function fetchUserinfoWithRetry(accessToken) {
  // The freshly-issued access token sometimes isn't propagated through
  // Clerk's backend by the time we immediately call /oauth/userinfo — one
  // retry after a short pause handles that race (mirrors auth_service.py).
  const keep = ["sub", "email", "name", "full_name", "given_name", "family_name", "picture", "org_id"];
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const raw = await fetchUserinfo(accessToken);
      const out = {};
      for (const k of keep) out[k] = raw[k];
      return out;
    } catch {
      if (attempt === 0) await new Promise((r) => setTimeout(r, 1000));
    }
  }
  return {};
}

function identityFromClaims(c) {
  return {
    user_id: c.sub,
    name: c.name || c.full_name,
    first_name: c.given_name,
    last_name: c.family_name,
    email: c.email,
    picture: c.picture,
  };
}

function claimsFromTokens(tokens) {
  const userinfo = tokens.userinfo || {};
  if (userinfo.sub) return identityFromClaims(userinfo);
  // Fallback: decode as JWT (Clerk may return JWTs on some plans).
  try {
    const payloadB64 = tokens.access_token.split(".")[1];
    const payload = JSON.parse(Buffer.from(payloadB64, "base64").toString("utf8"));
    if (!payload.sub) return null;
    return identityFromClaims(payload);
  } catch {
    return null;
  }
}

// CONXA_DEV_SKIP_AUTH=1 skips Clerk login entirely — unpackaged dev runs only, never honored in a
// packaged build. Mirrors Build Studio's auth_service.py flag of the same name.
function devSkipAuth() {
  return !app.isPackaged && ["1", "true", "yes"].includes((process.env.CONXA_DEV_SKIP_AUTH || "").trim().toLowerCase());
}

function devLogin() {
  const userinfo = { sub: "dev-user", name: "Dev User", email: "dev@conxa.local" };
  saveTokens({ access_token: "dev-bypass-token", refresh_token: "", exp: Date.now() / 1000 + 86400 * 365, userinfo });
  return claimsFromTokens({ userinfo });
}

async function login() {
  if (devSkipAuth()) return devLogin();
  if (!clerkDomain() || !clientId()) {
    // ponytail: dev-only bypass, real Clerk app not wired up yet — remove once CONXA_EXECUTE_CLERK_DOMAIN/CLIENT_ID are set
    if (!app.isPackaged) return devLogin();
    throw new Error("auth_not_configured");
  }
  const { verifier, challenge } = pkcePair();
  const state = base64url(crypto.randomBytes(16));

  let resolveCode, rejectCode;
  const codePromise = new Promise((res, rej) => {
    resolveCode = res;
    rejectCode = rej;
  });

  const { server, port } = await findServer((req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    if (url.pathname !== "/cb") {
      res.writeHead(204);
      res.end();
      return;
    }
    const code = url.searchParams.get("code");
    const gotState = url.searchParams.get("state");
    const error = url.searchParams.get("error");
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    if (code && gotState === state) {
      res.end("<html><body><h1>Signed in to CONXA</h1><p>You can close this window.</p></body></html>");
      resolveCode(code);
    } else {
      const msg = error || "state_mismatch";
      res.end(`<html><body><h1>Sign-in failed</h1><p>${msg}</p></body></html>`);
      rejectCode(new Error(msg));
    }
  });

  const redirectUri = `http://127.0.0.1:${port}/cb`;
  const authorizeUrl =
    `${clerkDomain()}/oauth/authorize?` +
    new URLSearchParams({
      response_type: "code",
      client_id: clientId(),
      redirect_uri: redirectUri,
      scope: "profile email offline_access user:org:read",
      state,
      code_challenge: challenge,
      code_challenge_method: "S256",
    }).toString();

  await shell.openExternal(authorizeUrl);

  let code;
  try {
    code = await Promise.race([
      codePromise,
      new Promise((_resolve, reject) => setTimeout(() => reject(new Error("login_timeout")), 300_000)),
    ]);
  } finally {
    server.close();
  }

  const tokens = await exchangeCode(code, verifier, redirectUri);
  tokens.userinfo = await fetchUserinfoWithRetry(tokens.access_token);
  const identity = claimsFromTokens(tokens);
  if (!identity || !identity.user_id) throw new Error("login_identity_missing");
  saveTokens(tokens);
  return identity;
}

async function getToken() {
  let tokens = loadTokens();
  if (!tokens) throw new Error("not_authenticated");
  if (Date.now() / 1000 >= Number(tokens.exp || 0) - REFRESH_LEEWAY_S) {
    if (!tokens.refresh_token) throw new Error("session_expired");
    const oldUserinfo = tokens.userinfo || {};
    tokens = await refreshTokens(tokens.refresh_token);
    tokens.userinfo = Object.keys(oldUserinfo).length ? oldUserinfo : await fetchUserinfoWithRetry(tokens.access_token);
    saveTokens(tokens);
  }
  return tokens.access_token;
}

async function currentIdentity() {
  const cached = loadTokens();
  if (!cached) return devSkipAuth() ? devLogin() : null;
  try {
    await getToken();
  } catch (err) {
    if (err.transient) {
      // A network blip or Clerk 5xx during refresh doesn't mean the user
      // signed out — show them as still signed in using the last-known
      // identity rather than bouncing them to the sign-in screen.
      const identity = claimsFromTokens(cached);
      return identity && identity.user_id ? identity : null;
    }
    return null; // refresh token missing/expired/revoked — genuinely signed out
  }
  const tokens = loadTokens();
  const identity = tokens ? claimsFromTokens(tokens) : null;
  return identity && identity.user_id ? identity : null;
}

module.exports = { login, logout, getToken, currentIdentity };
