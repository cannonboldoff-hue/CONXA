"use strict";
/**
 * access_refused.js — workspaces whose installed keys the cloud refused (HTTP 401/403) on skill
 * sync (sync.js) or run reports (tracker.js). Both used to only log it, so a person whose key was
 * reset saw normal "Done" answers and never learned why updates and reports stopped. server.js
 * reads it to add one plain sentence to list_skills and to every run answer for that workspace.
 *
 * In memory only: every start syncs again, and the next accepted call clears the mark.
 */

const _refused = new Map(); // workspace_id -> Set of "sync" | "tracking"

// status: the HTTP status the cloud answered. Other statuses (network blip, 5xx, 429) say nothing
// about the key, so they leave the mark as it is.
function noteAccess(workspaceId, source, status) {
  if (!workspaceId) return;
  const set = _refused.get(workspaceId) || new Set();
  if (status === 401 || status === 403) set.add(source);
  else if (status >= 200 && status < 300) set.delete(source);
  else return;
  if (set.size) _refused.set(workspaceId, set);
  else _refused.delete(workspaceId);
}

function isRefused(workspaceId) {
  return _refused.has(workspaceId);
}

function refusedWorkspaces() {
  return [..._refused.keys()];
}

const REFUSED_MESSAGE =
  "Conxa could not confirm this computer's access: the key in this installation was not accepted. " +
  "The workflows already installed still run, but updates and run reports cannot get through. " +
  "To fix it, ask whoever gave you these workflows for a new installer and run it.";

function _reset() { _refused.clear(); }

module.exports = { noteAccess, isRefused, refusedWorkspaces, REFUSED_MESSAGE, _reset };
