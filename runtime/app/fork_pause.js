"use strict";
// fork_pause.js — the pause/resume contract for an outcome fork that needs a PERSON to decide
// (an unreviewed consequential option like Replace, a policy of "ask me", or a popup nobody
// recorded). A sibling of review_pause.js (ai_review) and handover.js: a planned pause, not a
// failure, so it never touches recovery_stage.js's escalation state or retry_budget.js's map.
//
// What differs from ai_review, and why:
// - The question is for a human. The response says so in plain words and carries
//   `_meta["conxa/fork"].requires_human = true`, so the Build Studio sandbox routes it to a
//   person (never to its LLM answerer) and an MCP agent is told to ask the user rather than
//   decide for them.
// - The answer is a choice id from a fixed list built from what was actually offered (a state's
//   own options, or the buttons visible on an unrecognised popup) plus "stop". Anything else is
//   an invalid answer and gets a bounded re-ask.
// - No page-drift check on resume: the person may well have clicked in the page themselves.
//   run.js::runForkAnswer looks at the page again and copes with that.

const { STOP_CHOICE } = require("./fork");

// Accept either {choice: "<id>"} or the bare id string -- an MCP agent will do both.
function validateForkAnswer(answer, validIds) {
  const raw = (answer && typeof answer === "object") ? answer.choice : answer;
  if (typeof raw !== "string" || !raw.trim()) {
    return { valid: false, choice: null, errors: ['answer must be {"choice": "<one of the listed choices>"}'] };
  }
  const choice = raw.trim();
  if (!validIds.includes(choice)) {
    return { valid: false, choice: null, errors: [`"${choice}" is not one of the listed choices: ${validIds.join(", ")}`] };
  }
  return { valid: true, choice, errors: [] };
}

function describeRole(role) {
  if (role === "consequential") return " (changes or removes data)";
  if (role === "stop") return "";
  return "";
}

// MCP response for a planned fork pause. `info` is run.js's forkPause payload
// ({title, text, reason, suggested, unknown, choices}).
async function buildForkRequest(page, stepIndex, info, opts = {}) {
  const url = (() => { try { return page.url(); } catch (_) { return ""; } })();
  const choices = Array.isArray(info.choices) ? info.choices : [];
  const validIds = choices.map((c) => c.id);
  const suggestedNote = info.knownFromMap ? "  [what other workflows on this app chose]" : "  [what was chosen when this was recorded]";
  const lines = choices.map((c) => `  - ${c.id}: ${c.label}${describeRole(c.role)}${c.id === info.suggested ? suggestedNote : ""}`);

  const why = info.unknown && info.knownFromMap
    ? info.reason === "consequential_unapproved"
      ? "this popup is known from other workflows on this app, but its choice that changes or removes data has not been approved to run automatically"
      : "this popup is known from other workflows on this app, but no one has said what to do about it here"
    : info.unknown
    ? "an unexpected popup appeared that this workflow was never taught about"
    : info.reason === "consequential_unapproved"
      ? "the popup offers a choice that can change or remove data, and nobody has approved doing that automatically"
      : "this workflow is set to ask a person each time";

  const retryNote = opts.validationErrors && opts.validationErrors.length
    ? `\n\nThe previous answer was not usable:\n${opts.validationErrors.map((e) => `- ${e}`).join("\n")}`
    : "";

  const header =
    `Execution paused at step ${stepIndex + 1}: a popup needs a decision from the person running this.\n` +
    `Page URL: ${url}\n` +
    `Popup: ${info.title || "(untitled)"}${info.text ? `\n${info.text}` : ""}\n\n` +
    `Why it stopped: ${why}.\n\n` +
    `ASK THE USER which option to take. Do not choose for them -- this is their data. Choices:\n` +
    `${lines.join("\n")}\n\n` +
    `Then resume by calling execute_skill again with:\n` +
    `  resume_from: ${stepIndex}\n` +
    `  review_results: { "${stepIndex}": { "choice": "<id from the list above>" } }${retryNote}`;

  const content = [{ type: "text", text: header }];
  const shot = await page.screenshot({ type: "jpeg", quality: 80 }).catch(() => null);
  if (shot) {
    content.push(
      { type: "text", text: "Current page:" },
      { type: "image", data: shot.toString("base64"), mimeType: "image/jpeg" }
    );
  }

  return {
    content,
    _meta: {
      "conxa/fork": {
        step_index: stepIndex,
        title: info.title || "",
        message: info.text || "",
        reason: info.reason || "",
        unknown: !!info.unknown,
        suggested: info.suggested || null,
        choices: choices.map((c) => ({ id: c.id, label: c.label, role: c.role })),
        requires_human: true,
      },
    },
  };
}

module.exports = { validateForkAnswer, buildForkRequest, STOP_CHOICE };
