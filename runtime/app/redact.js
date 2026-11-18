"use strict";

// Keep secrets out of the runtime's logs (WF-8 secrets check). Mirrors conxa_core/redact.py: in every web address a
// query or fragment value is replaced when its name looks secret, and any value of a secret-looking run input (a
// password, a token) is replaced wherever it appears in a line. The values are held in memory only.

const REDACTED = "REDACTED";
const PARAM_PARTS = new Set(["pw", "pwd", "pass", "passwd", "password", "passcode", "passphrase", "secret", "token", "tok",
  "key", "apikey", "auth", "authorization", "session", "sessionid", "sid", "code", "otp", "pin", "cvv", "cvc", "sig",
  "signature", "jwt", "bearer", "credential", "credentials", "accesstoken", "refreshtoken", "idtoken"]);
const INPUT_PARTS = new Set(["pw", "pwd", "pass", "passwd", "password", "passcode", "passphrase", "secret", "token", "tok",
  "apikey", "otp", "cvv", "cvc", "credential", "credentials"]);
// Short, common words that mean a secret only as the whole name ("?code=…"), never inside one ("country_code").
const WHOLE_NAME_ONLY = new Set(["code", "key", "pin", "sid", "session", "auth", "sig"]);
const HINTS = ["password", "passwd", "token", "secret", "apikey"];
const URL_RE = /https?:\/\/[^\s"'<>\\]+/gi;

function looksSecret(name, parts) {
  const words = String(name || "").replace(/([a-z0-9])([A-Z])/g, "$1 $2").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  const joined = words.join("");
  return parts.has(joined) || words.some((w) => parts.has(w) && !WHOLE_NAME_ONLY.has(w)) || HINTS.some((h) => joined.includes(h));
}
const isSecretParam = (name) => looksSecret(name, PARAM_PARTS);
const isSecretInput = (name) => looksSecret(name, INPUT_PARTS);

const secretValues = new Set();
// A run's secret-looking inputs: their values are replaced in every later log line.
function noteSecrets(inputs) {
  for (const [k, v] of Object.entries(inputs || {})) {
    if (typeof v === "string" && v.length >= 4 && isSecretInput(k)) secretValues.add(v);
  }
}

function decode(s) {
  try { return decodeURIComponent(String(s).replace(/\+/g, " ")); } catch (_) { return String(s); }
}

function scrubPairs(raw) {
  return raw.split("&").map((seg) => {
    const at = seg.indexOf("=");
    if (at < 0) return seg;
    const name = seg.slice(0, at);
    const value = seg.slice(at + 1);
    return value && (isSecretParam(decode(name)) || secretValues.has(decode(value))) ? `${name}=${REDACTED}` : seg;
  }).join("&");
}

function scrubUrl(url) {
  const s = String(url);
  if (!s.includes("?") && !s.includes("#")) return s;
  const hashAt = s.indexOf("#");
  let head = hashAt >= 0 ? s.slice(0, hashAt) : s;
  let frag = hashAt >= 0 ? s.slice(hashAt + 1) : null;
  const q = head.indexOf("?");
  if (q >= 0) head = head.slice(0, q + 1) + scrubPairs(head.slice(q + 1));
  if (frag !== null && frag.includes("=")) frag = scrubPairs(frag);
  return frag === null ? head : `${head}#${frag}`;
}

// One log line with every address scrubbed and every noted secret value (as typed, or encoded in an address) gone.
function scrubLine(line) {
  let out = String(line).replace(URL_RE, (u) => scrubUrl(u));
  for (const v of secretValues) {
    for (const form of new Set([v, encodeURIComponent(v), JSON.stringify(v).slice(1, -1)])) {
      if (form) out = out.split(form).join(REDACTED);
    }
  }
  return out;
}

module.exports = { REDACTED, isSecretInput, isSecretParam, noteSecrets, scrubLine, scrubUrl };
