"use strict";
/**
 * Local chat-session store — thin wrapper over the ported opencode storage
 * service (../vendor/opencode/storage/storage.js). Two keys per session,
 * matching opencode's convention: session/<id>.json (metadata) and
 * message/<id>.json (the flat messages array run_turn.js reads/returns).
 *
 * Works in every mode, including BYOK — this is local-only persistence and
 * never calls the backend. (Signing in to CONXA is required to reach any
 * mode including BYOK, but BYOK's chat requests still go straight from this
 * device to your own model endpoint, never through conxa-execute's backend.)
 * Chats are never sent to the backend: they stay on this device only.
 */
const crypto = require("crypto");
const storage = require("../vendor/opencode/storage/storage");

async function listSessions() {
  const keys = await storage.list(["session"]);
  const sessions = await Promise.all(keys.map((k) => storage.read(k)));
  // Untitled chats have no messages yet — keep them out of the sidebar.
  return sessions.filter((s) => s.title !== "New chat").sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""));
}

async function createSession() {
  const id = crypto.randomUUID();
  const now = new Date().toISOString();
  const record = { id, title: "New chat", createdAt: now, updatedAt: now };
  await storage.write(["session", id], record);
  await storage.write(["message", id], []);
  return record;
}

async function loadSession(id) {
  const record = await storage.read(["session", id]);
  let messages = [];
  try {
    messages = await storage.read(["message", id]);
  } catch (e) {
    if (!(e instanceof storage.NotFoundError)) throw e;
  }
  return { ...record, messages };
}

async function saveSessionMessages(id, messages) {
  await storage.write(["message", id], messages);
  await storage.update(["session", id], (draft) => {
    draft.updatedAt = new Date().toISOString();
    if (draft.title === "New chat") {
      const first = messages.find((m) => m.role === "user");
      const text = first && (typeof first.content === "string"
        ? first.content
        : (first.content || []).map((p) => p.text || "").join(" "));
      const title = String(text || "").replace(/\s+/g, " ").trim().slice(0, 40);
      if (title) draft.title = title;
    }
  });
}

async function deleteSession(id) {
  await storage.remove(["session", id]);
  await storage.remove(["message", id]);
}

async function renameSession(id, title) {
  const clean = String(title || "").replace(/\s+/g, " ").trim().slice(0, 80);
  if (!clean) throw new Error("A chat needs a name.");
  await storage.update(["session", id], (draft) => { draft.title = clean; });
  return clean;
}

function _text(m) {
  return typeof m.content === "string" ? m.content : (m.content || []).map((p) => p.text || "").join(" ");
}

// ponytail: linear scan of every local transcript; add an index if chat counts get large.
async function searchSessions(query, limit = 30) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return [];
  const hits = [];
  for (const s of await listSessions()) {
    let snippet = "";
    if (!s.title.toLowerCase().includes(q)) {
      let messages = [];
      try { messages = await storage.read(["message", s.id]); } catch { /* no transcript yet */ }
      for (const m of messages) {
        // Hidden run-update notes and tool output aren't something the user wrote or read.
        if (m.role !== "user" && m.role !== "assistant") continue;
        const t = _text(m).replace(/\s+/g, " ");
        if (t.startsWith("[Conxa run update]")) continue;
        const at = t.toLowerCase().indexOf(q);
        if (at === -1) continue;
        const start = Math.max(0, at - 40);
        snippet = `${start ? "…" : ""}${t.slice(start, at + q.length + 60).trim()}${at + q.length + 60 < t.length ? "…" : ""}`;
        break;
      }
      if (!snippet) continue;
    }
    hits.push({ ...s, snippet });
    if (hits.length >= limit) break;
  }
  return hits;
}

module.exports = { listSessions, createSession, loadSession, saveSessionMessages, deleteSession, renameSession, searchSessions };
