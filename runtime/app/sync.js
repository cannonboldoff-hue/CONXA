"use strict";
const fs     = require("fs");
const path   = require("path");
const crypto = require("crypto");
const httpClient = require("./http_client");
const artifactStore = require("./artifact_store");
const { ARTIFACT_INDEX_FILE } = artifactStore;

// Prefer the host-bridged instance (bootstrap.js sets this) so every layer shares one
// implementation of the junction-handling logic; fall back to a local copy for direct
// `node server.js` dev runs or test fixtures that never went through bootstrap.js.
const versionManager = require("./host_bridge").versionManager();

// Thin wrappers over the shared implementations in http_client.js — same
// defaults these functions used to hardcode (3s JSON / 8s download, optional
// Bearer token, 304 → empty file list, lenient status handling on downloads).
function _fetchJSON(url, token, timeoutMs) {
  return httpClient.fetchJSON(url, {
    token,
    onNotModified: { files: [] },
    timeoutMs: timeoutMs || 3000,
  });
}

function _downloadBuffer(url, timeoutMs) {
  return httpClient.downloadBuffer(url, { requireOkStatus: false, timeoutMs: timeoutMs || 8000 });
}

// Write to .tmp, verify SHA-256, rename atomically
function atomicWrite(targetPath, content, expectedSha256) {
  const tmpPath = targetPath + ".tmp";
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  fs.writeFileSync(tmpPath, content);
  const actual = crypto.createHash("sha256").update(content).digest("hex");
  if (actual !== expectedSha256) {
    try { fs.unlinkSync(tmpPath); } catch (_) {}
    throw new Error(`Checksum mismatch for ${path.basename(targetPath)}: expected ${expectedSha256}`);
  }
  fs.renameSync(tmpPath, targetPath);
}

// version_manager.activate() already keeps the last few versions on disk, so rollback
// never needs a backup-copy dance the way flat-file sync used to — a failed activation
// just leaves `current` pointing at whatever it pointed at before.

async function _syncCompany(skillPacksDir, workspace_id, log, artifactTasks = null) {
  const packPath = path.join(skillPacksDir, workspace_id, "pack.json");
  if (!fs.existsSync(packPath)) return;

  let pack;
  try { pack = JSON.parse(fs.readFileSync(packPath, "utf8")); } catch (_) { return; }

  const syncEndpoint = pack.sync_endpoint;
  if (!syncEndpoint) return;

  const token = pack.sync_token || null;
  if (!token) {
    log(`[sync:warn] ${workspace_id} no sync_token in pack.json — skipping sync (pack may need to be republished)`);
    return;
  }

  // Recency check: skip if synced within the last 5 minutes
  if (pack.last_synced) {
    const ageMs = Date.now() - new Date(pack.last_synced).getTime();
    if (ageMs < 5 * 60 * 1000) {
      log(`[sync:skip] ${workspace_id} synced ${Math.floor(ageMs / 1000)}s ago — skipping`);
      return;
    }
  }

  // Each skill is compared independently against its own last-known version (read
  // from its own version.json, not one shared workspace-wide counter), so republishing
  // one skill never triggers a redownload of skills that haven't changed.
  //
  // Version lookup only ever consults the group-nested path (skill_groups[slug],
  // falling back to "_default"), never the pre-Groups flat `workspace_id/slug/` layout.
  // That's deliberate: a skill that's only ever been synced to the old flat location
  // has no version.json at its nested path, so it reads back as version "0" here,
  // the server reports action:"update", and the file gets freshly written into the
  // new nested location below — a one-time forced resync per skill instead of a
  // separate migration pass.
  const skillGroups = pack.skill_groups || {};
  const sinceMap = {};
  for (const slug of pack.skills || []) {
    const group = skillGroups[slug] || "_default";
    const skillRoot = path.join(skillPacksDir, workspace_id, group, slug);
    const currentDir = versionManager.resolveCurrent(skillRoot);
    let version = "0";
    if (currentDir) {
      try { version = JSON.parse(fs.readFileSync(path.join(currentDir, "version.json"), "utf8")).skill_version || "0"; }
      catch (_) {}
    }
    sinceMap[slug] = version;
  }

  let delta;
  {
    const url = `${syncEndpoint}?since=${encodeURIComponent(JSON.stringify(sinceMap))}`;
    let lastErr;
    for (const waitMs of [0, 300]) {
      if (waitMs) await new Promise((r) => setTimeout(r, waitMs));
      try {
        delta = await _fetchJSON(url, token, 3000);
        lastErr = null;
        break;
      } catch (e) {
        lastErr = e;
      }
    }
    if (lastErr) {
      log(`[sync:error] ${workspace_id} delta fetch failed — ${lastErr.message}`);
      return;
    }
  }

  // The delta response is authoritative for "what skills does this workspace have
  // right now" — every server-side skill gets an entry (no_change or update), so
  // this is always the full current skill list, independent of what this client's
  // sinceMap knew about beforehand. Applying it unconditionally below (even when
  // nothing changed) is what makes a skill added to a workspace post-install ever
  // reach skill_loader.js's registry, which reads pack.skills off disk rather than
  // scanning the directory tree itself — a thin installer starts with pack.skills
  // empty, so without this, no skill would ever become visible after install.
  const serverSkillNames = (delta.skills || []).map((s) => s.name).filter(Boolean);
  const changed = (delta.skills || []).filter((s) => s.action === "update");

  // {skill_slug: {code, at}} — reported to the cloud via server.js's phone-home
  // (see runtime/sync_errors.js) so the Deployment dashboard can show a real
  // "failed" status. Seeded from last round so an error survives an unrelated
  // sync pass; cleared the moment the skill activates successfully below.
  const syncErrors = { ...(pack.last_sync_errors || {}) };

  const activated = [];
  if (changed.length > 0) {
    // Download all files for all changed skills in parallel first — nothing touches
    // disk until every buffer is in hand, so a mid-batch network failure never leaves
    // a skill half-written.
    let downloaded = null;
    try {
      downloaded = await Promise.all(changed.map(async (skillEntry) => {
        const files = await Promise.all((skillEntry.files || []).map(async (fileEntry) => {
          let content;
          if (fileEntry.content_base64) {
            content = Buffer.from(fileEntry.content_base64, "base64");
          } else if (fileEntry.content_url) {
            content = await _downloadBuffer(fileEntry.content_url, 8000);
          } else {
            throw new Error(`no content source for ${skillEntry.name}/${fileEntry.path}`);
          }
          return { fileEntry, content };
        }));
        return { skillEntry, files };
      }));
    } catch (e) {
      log(`[sync:error] ${workspace_id} download failed — ${e.message}`);
      const at = new Date().toISOString();
      for (const skillEntry of changed) syncErrors[skillEntry.name] = { code: "download_failed", at };
    }

    for (const { skillEntry, files } of downloaded || []) {
      const slug = skillEntry.name;
      const group = skillEntry.group || "_default";
      const rawVersion = String(skillEntry.version || "0");
      const versionDirName = /^v/.test(rawVersion) ? rawVersion : `v${rawVersion}`;
      const skillRoot  = path.join(skillPacksDir, workspace_id, group, slug);
      const versionDir = path.join(skillRoot, versionDirName);
      try {
        // Clear any stale partial staging from a previously interrupted attempt at
        // this exact version before writing fresh files into it.
        try { fs.rmSync(versionDir, { recursive: true, force: true }); } catch (_) {}
        for (const { fileEntry, content } of files) {
          atomicWrite(path.join(versionDir, fileEntry.path), content, fileEntry.sha256);
        }
        if (!fs.existsSync(path.join(versionDir, "version.json"))) {
          fs.writeFileSync(path.join(versionDir, "version.json"), JSON.stringify({
            skill_version: skillEntry.version,
            released_at: new Date().toISOString(),
          }));
        }
        versionManager.activate(skillRoot, versionDir, { keep: 3, requiredFiles: ["manifest.json"] });
        activated.push(`${slug}@${skillEntry.version}`);
        delete syncErrors[slug];
      } catch (e) {
        log(`[sync:error] ${workspace_id}/${slug}: activation failed — ${e.message}`);
        try { fs.rmSync(versionDir, { recursive: true, force: true }); } catch (_) {}
        syncErrors[slug] = {
          code: /checksum mismatch/i.test(e.message) ? "checksum_mismatch" : "activation_failed",
          at: new Date().toISOString(),
        };
      }
    }
  }

  // Written unconditionally on any successful delta fetch — this is also what makes
  // the 5-minute recency-skip above actually engage on a no-op sync; previously the
  // write only happened when something was newly activated.
  pack.skills = serverSkillNames;
  // Persisted so the *next* sync's sinceMap computation above (which only ever reads
  // the nested path) knows each skill's group without needing the delta again.
  pack.skill_groups = {};
  for (const s of delta.skills || []) {
    if (s.name) pack.skill_groups[s.name] = s.group || "_default";
  }
  pack.last_synced = new Date().toISOString();
  pack.last_sync_errors = syncErrors;
  const packTmp = packPath + ".tmp";
  fs.writeFileSync(packTmp, JSON.stringify(pack, null, 2));
  fs.renameSync(packTmp, packPath);

  // Best-effort: keeps each registered agent host's discoverability file
  // (SKILL.md, AGENTS.md, …) in sync with the workspace's current skill list.
  // Never fails the sync itself over this — registration already succeeded
  // and skills are already usable even if a host's instructions file couldn't
  // be updated this round.
  try {
    await require("./durable_context").updateDurableContext(workspace_id, serverSkillNames, _appsBySkill(skillPacksDir, workspace_id, pack.skill_groups));
  } catch (e) {
    log(`[sync:warn] ${workspace_id} durable-context update failed — ${e.message}`);
  }

  if (activated.length > 0) {
    log(`[sync:status] ${workspace_id} updated (${activated.length} skill${activated.length !== 1 ? "s" : ""}: ${activated.join(", ")})`);
  } else if (changed.length === 0) {
    log(`[sync:status] ${workspace_id} up-to-date`);
  } else {
    log(`[sync:status] ${workspace_id} sync completed with no successful activations`);
  }

  // Hand the caller pass two's work. Everything above is pass one: the code files, which
  // execution genuinely needs — the gate opens the moment they activate. Artifacts are only
  // ever read when a step FAILS and reaches the agent recovery tier, so making anyone wait on
  // them would be charging every run for something almost no run uses.
  //
  // Collected here rather than started here: this function runs inside syncSkillPacks' hard
  // timeout, and pass two must outlive it (see syncSkillPacks).
  if (artifactTasks) {
    for (const s of delta.skills || []) {
      if (!s || !s.name || !Array.isArray(s.artifacts) || !s.artifacts.length) continue;
      const url = _artifactEndpoint(syncEndpoint, s.name);
      if (!url) continue;  // legacy unversioned sync endpoint — no artifact route exists for it
      const skillRoot = path.join(skillPacksDir, workspace_id, s.group || "_default", s.name);
      artifactTasks.push({
        workspace_id, slug: s.name, url, token,
        artifacts: s.artifacts,
        // Resolved after activation above, so an updated skill gets its NEW version directory.
        skillDir: versionManager.resolveCurrent(skillRoot),
      });
    }
  }
}

// The artifact route is the versioned delta's sibling:
//   .../workflows/{gen}/{ws}/skill-packs/delta  →  .../workflows/{gen}/{ws}/skill-packs/{slug}/artifacts
// A pack still carrying the legacy unversioned `/skill-packs/{ws}/delta` endpoint has no
// artifact route to derive, and gets none — republishing moves it onto the versioned form.
function _artifactEndpoint(syncEndpoint, slug) {
  const base = String(syncEndpoint || "").split("?")[0];
  if (!/\/workflows\/[^/]+\/[^/]+\/skill-packs\/delta$/.test(base)) return null;
  return base.replace(/\/delta$/, `/${encodeURIComponent(slug)}/artifacts`);
}

/**
 * Pass two: fetch the recovery artifacts this machine is missing, one skill at a time.
 *
 * One request per skill, carrying the hashes already in the store — so the response holds
 * exactly the difference, and first install (send nothing, receive everything) and a routine
 * update (send all but three, receive three) are the same code path. One request per skill also
 * means there is no concurrency policy to invent: the burst that would otherwise hit a small
 * cloud instance is simply not created.
 *
 * Nothing here may fail the sync, block execution, or record a sync error. A missing screenshot
 * costs the agent tier one signal out of several; a sync marked failed would make a working
 * skill look broken. Interruption is free — the next sync diffs against the store again and asks
 * for whatever is still absent.
 */
async function _syncArtifacts(skillPacksDir, tasks, log) {
  const AdmZip = require("./host_bridge").hostRequire("adm-zip");
  for (const task of tasks) {
    try {
      const have = artifactStore.haveHashes(skillPacksDir, task.artifacts);
      if (have.length >= task.artifacts.length) continue;  // nothing missing for this skill

      const res = await httpClient.postForBuffer(task.url, { token: task.token, json: { have }, timeoutMs: 60000 });
      const expected = String(res.headers["x-artifact-sha256"] || "").toLowerCase();
      if (expected) {
        const actual = crypto.createHash("sha256").update(res.body).digest("hex");
        if (actual !== expected) throw new Error(`archive checksum mismatch: expected ${expected}, got ${actual}`);
      }
      if (!res.body.length) continue;

      const wanted = new Map(task.artifacts.map(a => [String(a.path), String(a.sha256 || "").toLowerCase()]));
      let stored = 0;
      for (const entry of new AdmZip(res.body).getEntries()) {
        if (entry.isDirectory) continue;
        const sha = wanted.get(entry.entryName);
        // Only files the listing vouched for are stored, under the hash the listing named —
        // artifact_store.put re-hashes and rejects a mismatch, so a zip cannot introduce a file
        // the delta never described, nor write one under someone else's hash.
        if (!sha) continue;
        artifactStore.put(skillPacksDir, sha, entry.entryName, entry.getData());
        stored++;
      }
      if (stored) log(`[sync:artifacts] ${task.workspace_id}/${task.slug} stored ${stored} artifact${stored !== 1 ? "s" : ""}`);
    } catch (e) {
      log(`[sync:artifacts:warn] ${task.workspace_id}/${task.slug} — ${e.message}`);
    }

    // The index, written whether or not anything was downloaded this round. The store is keyed
    // by CONTENT, but recovery only knows an artifact's PATH (`visual_ref` in recovery.json), so
    // without this mapping the bytes would be on disk and unreachable. Separate try/catch: a
    // failed download must not also cost the index for artifacts that were already stored.
    try {
      if (task.skillDir) {
        fs.writeFileSync(
          path.join(task.skillDir, ARTIFACT_INDEX_FILE),
          JSON.stringify({ artifacts: task.artifacts }),
        );
      }
    } catch (e) {
      log(`[sync:artifacts:warn] ${task.workspace_id}/${task.slug} — ${e.message}`);
    }
  }
}

// {slug: host} for the skills whose active manifest names a start URL -- so an agent's
// discoverability note can say which web app each workflow works in. Best effort and local only:
// a skill that is not on disk yet (or has no URL) is simply listed without an app.
function _appsBySkill(skillPacksDir, workspaceId, skillGroups) {
  const appOf = require("./app_knowledge").appOfManifest;
  const out = {};
  for (const [slug, group] of Object.entries(skillGroups || {})) {
    try {
      const manifest = JSON.parse(fs.readFileSync(path.join(skillPacksDir, workspaceId, group, slug, "current", "manifest.json"), "utf8"));
      const app = appOf(manifest);
      if (app) out[slug] = app;
    } catch (_) { /* not synced yet */ }
  }
  return out;
}

async function _doSync(skillPacksDir, log, artifactTasks = null) {
  if (!fs.existsSync(skillPacksDir)) return;
  const workspaceIds = fs.readdirSync(skillPacksDir);
  await Promise.allSettled(
    workspaceIds.map(workspaceId => _syncCompany(skillPacksDir, workspaceId, log, artifactTasks))
  );
}

// Public: run sync with a hard timeout.
// Default 4s — skill packs are small JSON files; parallel downloads complete well within this.
//
// Two passes, and only the first one is allowed to hold anyone up:
//
//   Pass one (awaited, inside the timeout) — the code files. Workflows cannot run without them,
//   so this is the gate, and it stays on the tight budget it always had.
//
//   Pass two (started after, deliberately NOT awaited) — recovery artifacts. Bytes, not
//   kilobytes, and read only when a step fails and reaches the agent tier, which for a healthy
//   skill is never. Awaiting them would make every start pay for something almost no run uses;
//   putting them inside the race would just get them killed at 4s, every time.
//
// `artifacts: false` skips pass two entirely. The install-time `sync` subcommand passes it: that
// process exists to exit, and leaving background work running would either hold the installer
// open or be killed halfway. Nothing is lost — the server's own startup sync picks them up.
async function syncSkillPacks(skillPacksDir, { timeoutMs = 4000, log = console.error, artifacts = true } = {}) {
  let timer;
  // Collected by reference rather than returned, so whatever pass one managed to finish is still
  // usable when the race below times out on an unrelated workspace.
  const artifactTasks = artifacts ? [] : null;
  try {
    await Promise.race([
      _doSync(skillPacksDir, log, artifactTasks),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("sync timeout")), timeoutMs); }),
    ]);
  } finally {
    // Promise.race doesn't cancel the loser: when _doSync wins (the normal case),
    // this timer stays scheduled and keeps the event loop — and any caller blocking
    // on this process, like the NSIS installer's install-time `sync` subcommand —
    // alive until it fires, up to timeoutMs later. Clear it either way.
    clearTimeout(timer);

    // In `finally`, so a timeout on one workspace still lets the workspaces that DID finish
    // fetch their artifacts. Not awaited, and its rejection is swallowed: pass two is an
    // optimisation on a path that already degrades gracefully — a step that fails before its
    // screenshot arrives just fetches that one file inline, and worst case the agent tier runs
    // without a reference image, exactly as it does today.
    if (artifactTasks && artifactTasks.length) {
      _syncArtifacts(skillPacksDir, artifactTasks, log)
        .catch((e) => log(`[sync:artifacts:warn] pass failed — ${e.message}`));
    }
  }
}

module.exports = { syncSkillPacks, _syncArtifacts, _artifactEndpoint };
