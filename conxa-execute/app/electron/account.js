"use strict";
/**
 * Per-person local data (AUTH-26). Chats, run history and settings live under
 * <userData>/accounts/<key>/ so a second person signing in on the same computer never sees the first
 * person's work. Signed out uses an empty holding folder. The sign-in itself (clerk-session.bin) stays
 * at the top level. `active_account.json` remembers the key between launches.
 */
const fs = require("fs");
const path = require("path");

const SIGNED_OUT = "_signed_out";
const POINTER = "active_account.json";
const MARKER = ".legacy_migrated.json";
// What sat directly in userData before per-account folders.
const LEGACY = ["storage", "run-history.json", "byo-settings.bin"];

function userData() {
  return process.env.CONXA_EXECUTE_USER_DATA || require("electron").app.getPath("userData");
}

/** Folder-safe key for a signed-in identity; the person, not the workspace they act for. */
function keyFromIdentity(identity) {
  const id = identity && identity.user_id ? String(identity.user_id) : "";
  if (!id) return SIGNED_OUT;
  const slug = id.replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "user";
  return slug.length > 40 ? `${slug.slice(0, 28)}_${require("crypto").createHash("sha1").update(id).digest("hex").slice(0, 10)}` : slug;
}

let current = null;

function readPointer() {
  try {
    const key = JSON.parse(fs.readFileSync(path.join(userData(), POINTER), "utf8")).account_key;
    return typeof key === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(key) ? key : SIGNED_OUT;
  } catch {
    return SIGNED_OUT;
  }
}

function key() {
  if (current === null) current = readPointer();
  return current;
}

/** The folder holding the current person's chats, history and settings. */
function dir() {
  return path.join(userData(), "accounts", key());
}

function hasFiles(p) {
  try {
    return fs.statSync(p).isDirectory() ? fs.readdirSync(p).length > 0 : true;
  } catch {
    return false;
  }
}

/** The first person to sign in after the update takes the earlier shared work (moved, never deleted). */
function migrateLegacy(nextKey) {
  const base = userData();
  const marker = path.join(base, MARKER);
  if (nextKey === SIGNED_OUT || fs.existsSync(marker)) return;
  const present = LEGACY.filter((n) => hasFiles(path.join(base, n)));
  if (!present.length) return;
  const target = path.join(base, "accounts", nextKey);
  if (hasFiles(path.join(target, "storage"))) return; // that person already has their own chats
  try {
    fs.mkdirSync(target, { recursive: true });
    for (const n of present) fs.renameSync(path.join(base, n), path.join(target, n));
    fs.writeFileSync(marker, JSON.stringify({ account_key: nextKey }));
  } catch (err) {
    console.error("account: could not move the earlier chats and history:", err);
  }
}

/** Point everything at this identity's folder (or the holding folder when null). Returns true when it changed. */
function apply(identity) {
  const next = keyFromIdentity(identity);
  if (next === key()) return false;
  migrateLegacy(next);
  fs.mkdirSync(userData(), { recursive: true });
  fs.writeFileSync(path.join(userData(), POINTER), JSON.stringify({ account_key: next }));
  current = next;
  return true;
}

module.exports = { SIGNED_OUT, keyFromIdentity, key, dir, apply };
