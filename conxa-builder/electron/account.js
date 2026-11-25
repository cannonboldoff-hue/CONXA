"use strict";
/**
 * Per-account local data (AUTH-26). The Python backend writes <studio home>/active_account.json
 * when the signed-in account changes; this module turns that file into the env the next backend
 * start uses, so each account's work lives in <studio home>/accounts/<key>/ and never shows for another.
 */
const fs = require("fs");
const path = require("path");

const SIGNED_OUT = "_signed_out";

/** The account key the backend last recorded, or "_signed_out" when there is none (or it is unreadable). */
function readAccountKey(studioHome) {
  try {
    const key = JSON.parse(fs.readFileSync(path.join(studioHome, "active_account.json"), "utf8")).account_key;
    return typeof key === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(key) ? key : SIGNED_OUT;
  } catch {
    return SIGNED_OUT;
  }
}

/**
 * Env that points the backend at one account's folder. Empty when the shell already set
 * SKILL_DATA_DIR (a developer or test harness choosing its own folder): no switching then.
 */
function accountEnv(studioHome, key) {
  if (process.env.SKILL_DATA_DIR) return {};
  const home = path.join(studioHome, "accounts", key);
  return { CONXA_ACCOUNT_HOME: home, SKILL_DATA_DIR: path.join(home, "data") };
}

module.exports = { SIGNED_OUT, readAccountKey, accountEnv };
