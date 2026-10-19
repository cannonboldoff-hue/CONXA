"use strict";
/**
 * Thin client for conxa-cloud — Conxa Execute has no backend of its own.
 * Two things live here: the personal/team context switcher (getContexts)
 * and the tool-calling chat transport plugged into
 * vendor/opencode/loop/run_turn.js (makeChatCompletion). Cloud's LLM proxy
 * is NOT a real OpenAI-compatible endpoint — it's Conxa's own {task,payload}
 * request shape, and its stream is tagged {"type":"text"|"tool_call",...}
 * chunks rather than OpenAI's wire format — so run_turn's default
 * fetch-based transport can't be pointed at it directly; this module
 * translates between the two.
 */
const authService = require("./auth_service");

function baseUrl() {
  return (process.env.CONXA_EXECUTE_API_BASE_URL || "https://apis.conxa.in").replace(/\/+$/, "");
}

async function authedFetch(path, options = {}) {
  const token = await authService.getToken(); // throws not_authenticated/session_expired
  return fetch(`${baseUrl()}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "X-Conxa-Client": "conxa-execute",
      ...(options.headers || {}),
    },
  });
}

/** { contexts: [{workspace_id, workspace_name, kind, credits_remaining}], active_workspace_id } */
async function getContexts() {
  // Bounded: the app's first screen waits on this call, and fetch() has no timeout of its own — an
  // unreachable server used to leave the window blank forever instead of reaching the error screen.
  let resp;
  try {
    resp = await authedFetch("/api/v1/execute/contexts", { signal: AbortSignal.timeout(15_000) });
  } catch (e) {
    if (e && (e.name === "TimeoutError" || e.name === "AbortError")) {
      throw new Error(`CONXA's servers didn't respond (${baseUrl()}).`);
    }
    throw e;
  }
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || `execute_api_http_${resp.status}`);
  return data;
}

/**
 * Builds a run_turn.js-compatible chatCompletion(body) closure bound to one
 * chat context — see run_turn.js's opts.chatCompletion docstring for the
 * contract it must satisfy: given an OpenAI-shaped {model, messages, tools?,
 * tool_choice?} body, resolve to {ok:true, json:{choices:[{message}]}} or
 * {ok:false, error}. If `onDelta` is given, it also fires synchronously with
 * {type:"text"|"reasoning", text} for each chunk as the stream arrives, so a
 * caller can paint the reply live — the eventual resolved value is
 * unaffected, still the full accumulated turn.
 */
function makeChatCompletion({ targetWorkspaceId, sessionId, timeoutMs, onDelta } = {}) {
  return async function chatCompletion(openaiBody) {
    let resp;
    try {
      resp = await authedFetch("/api/v1/llm/proxy/text/stream", {
        method: "POST",
        body: JSON.stringify({
          task: "execute_chat",
          usage_class: "execute_chat",
          timeout_ms: timeoutMs || 60_000,
          ...(targetWorkspaceId ? { target_workspace_id: targetWorkspaceId } : {}),
          payload: {
            messages: openaiBody.messages,
            ...(sessionId ? { session_id: sessionId } : {}),
            ...(openaiBody.tools ? { tools: openaiBody.tools, tool_choice: openaiBody.tool_choice } : {}),
          },
        }),
      });
    } catch (e) {
      return { ok: false, error: `CONXA's servers are unreachable right now: ${e.message}` };
    }
    if (!resp.ok || !resp.body) {
      let detail = "";
      try {
        detail = (await resp.json()).detail || "";
      } catch {
        // non-JSON error body
      }
      return { ok: false, error: detail || `execute_api_http_${resp.status}` };
    }

    // OpenAI streaming semantics: each tool_call delta carries an index
    // (which call it belongs to) and the pieces (id/name once, arguments in
    // fragments) accumulate into that index's entry.
    const toolCalls = new Map();
    let text = "";
    let streamError = "";
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf("\n\n")) !== -1) {
        const line = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        if (!line.startsWith("data:")) continue;
        let evt;
        try {
          evt = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (evt.error) {
          streamError = evt.error;
        } else if (evt.type === "text") {
          text += evt.text;
          if (onDelta) onDelta({ type: "text", text: evt.text });
        } else if (evt.type === "reasoning") {
          if (onDelta) onDelta({ type: "reasoning", text: evt.text });
        } else if (evt.type === "tool_call") {
          const i = evt.index || 0;
          const call = toolCalls.get(i) || { id: null, type: "function", function: { name: null, arguments: "" } };
          if (evt.id) call.id = evt.id;
          if (evt.name) call.function.name = evt.name;
          if (evt.arguments_delta) call.function.arguments += evt.arguments_delta;
          toolCalls.set(i, call);
        }
      }
    }

    if (streamError && !text && toolCalls.size === 0) {
      return { ok: false, error: streamError };
    }
    const message = { role: "assistant", content: text };
    if (toolCalls.size) {
      message.tool_calls = [...toolCalls.keys()].sort((a, b) => a - b).map((i) => toolCalls.get(i));
    }
    return { ok: true, json: { choices: [{ message }] } };
  };
}

module.exports = { getContexts, makeChatCompletion, baseUrl };
