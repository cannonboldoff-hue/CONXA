"use strict";
/**
 * Conxa Execute — Electron main process.
 * Window + IPC only. No Python backend, no recorder.
 */

const { app, BrowserWindow, desktopCapturer, ipcMain, Menu, screen, shell } = require("electron");
const { autoUpdater } = require("electron-updater");
const crypto = require("crypto");
const net = require("net");
const path = require("path");
const mcp = require("./mcp_client");
const { resolveRuntimeCommand } = require("./runtime_path");
const { classifyRunResult, extractRunId } = require("./classify");
const { waitForRunEnd, TERMINAL, FOLLOW_UP_PREFIX } = require("./run_follow");
const settings = require("./settings");
const history = require("./history");
const account = require("./account");
const sessionsStore = require("./sessions");
const authService = require("./auth_service");
const executeClient = require("./execute_client");
const browserPanel = require("./browser_panel");
const browserControl = require("./browser_control");
const compaction = require("../vendor/opencode/session/compaction");
const { runTurn } = require("../vendor/opencode/loop/run_turn");

const IS_DEV = !app.isPackaged;
process.env.CONXA_ELECTRON_IS_PACKAGED = app.isPackaged ? "1" : "0";

// electron-builder's extraMetadata bakes public Clerk OAuth config into the packaged
// package.json (a packaged app doesn't inherit the builder's shell env) — promote it into
// process.env here so auth_service.js's existing env-var reads pick it up unchanged.
const pkg = require("../package.json");
if (!process.env.CONXA_EXECUTE_CLERK_DOMAIN && pkg.conxaExecuteClerkDomain) {
  process.env.CONXA_EXECUTE_CLERK_DOMAIN = pkg.conxaExecuteClerkDomain;
}
if (!process.env.CONXA_EXECUTE_CLERK_CLIENT_ID && pkg.conxaExecuteClerkClientId) {
  process.env.CONXA_EXECUTE_CLERK_CLIENT_ID = pkg.conxaExecuteClerkClientId;
}

// Ask the OS for an unused loopback port before Electron reads command line switches —
// --remote-debugging-port needs a concrete number, not 0 (Electron doesn't surface the
// auto-assigned port anywhere reachable from this process). Async because a listening
// socket only actually has a port once the "listening" event fires, not the instant
// .listen() returns — but appendSwitch only needs to land before app.whenReady()
// resolves, so awaiting this ahead of that call is enough.
function _freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
  });
}
let _CDP_PORT = null;
const _cdpPortReady = _freePort().then((port) => {
  _CDP_PORT = port;
  app.commandLine.appendSwitch("remote-debugging-port", String(port));
  app.commandLine.appendSwitch("remote-debugging-address", "127.0.0.1");
});

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
  process.exit(0);
}

let mainWindow = null;

function windowFromEvent(event) {
  return BrowserWindow.fromWebContents(event.sender);
}

function createWindow() {
  const buildDir = path.join(__dirname, "..", "build");
  const iconPath = process.platform === "win32"
    ? path.join(buildDir, "icon.ico")
    : path.join(buildDir, "icon.png");
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: "#141413",
    title: "CONXA",
    icon: iconPath,
    frame: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (IS_DEV && process.env.CONXA_RENDERER_URL) {
    mainWindow.loadURL(process.env.CONXA_RENDERER_URL);
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "renderer", "dist", "index.html"));
  }

  if (IS_DEV) {
    mainWindow.webContents.on("before-input-event", (_event, input) => {
      const isToggle =
        input.key === "F12" || (input.control && input.shift && input.key.toLowerCase() === "i");
      if (isToggle) mainWindow.webContents.toggleDevTools();
    });
  }

  const sendMaximizeState = () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    mainWindow.webContents.send("window:maximized", mainWindow.isMaximized());
  };
  mainWindow.on("maximize", sendMaximizeState);
  mainWindow.on("unmaximize", sendMaximizeState);
  // A minimized window lays its views out at 0x0; the panel holds a run's page at size meanwhile.
  mainWindow.on("minimize", () => void browserPanel.setMinimized(true));
  mainWindow.on("restore", () => void browserPanel.setMinimized(false));
  // A chat turn parked on a confirm card must not hang forever if the window goes away.
  mainWindow.on("closed", () => settleConfirms(false));

  browserPanel.init(mainWindow, {
    onTabsChanged: (runId, tabs, meta) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("panel:tabs", { runId, tabs, chatId: browserPanel.chatOf(runId), ...meta });
      }
    },
  });
}

const RUNTIME_MISSING_MESSAGE = "Conxa runtime is not installed. Install a skill pack first, then reopen CONXA.";

// One place to turn an internal error code into words a person can act on.
// Anything not listed here falls back to a generic sentence rather than the
// raw e.message — an MCP SDK string, an HTTP status, or a stack trace is not
// something a user can do anything with.
const ERROR_MESSAGES = {
  runtime_missing: RUNTIME_MISSING_MESSAGE,
  runtime_bad_reply: null, // mcp_client.js already writes a full human sentence for this one
  not_signed_in: "Sign in to CONXA in Settings to use this mode.",
  not_authenticated: "Your CONXA sign-in expired. Sign in again in Settings.",
  session_expired: "Your CONXA sign-in expired. Sign in again in Settings.",
  login_timeout: "Sign-in timed out. Click Sign in to try again.",
  login_cancelled: "", // the person pressed Cancel: not an error, nothing to say
  auth_not_configured: "Sign-in isn't set up in this build.",
  login_identity_missing: "CONXA couldn't read your account details after sign-in. Try again.",
  execute_context_not_available: "You don't have Conxa Execute access in that workspace anymore.",
  llm_all_providers_failed: "Conxa couldn't reach its AI service just now. Try again in a moment.",
  llm_reasoning_only_no_content: "The AI didn't write an answer this time. Try again.",
  workspace_concurrency_limit: "This workspace is busy with other requests. Try again in a few seconds.",
  no_session: "No active chat session.",
  panel_empty: "Nothing is open in the browser panel yet.",
};

function humanMessage(code, fallbackMessage) {
  if (Object.prototype.hasOwnProperty.call(ERROR_MESSAGES, code)) {
    return ERROR_MESSAGES[code] || fallbackMessage;
  }
  if (/^execute_api_http_/.test(code || "")) {
    return "CONXA's servers are unreachable right now. Try again in a moment.";
  }
  return "Something went wrong. Try again, and if it keeps happening restart CONXA.";
}

function fail(code, message) {
  return { ok: false, code, message: message || humanMessage(code) };
}

// Wraps an IPC handler so every failure path returns the same
// { ok:false, code, message } shape with a human sentence, and every error is
// still logged to the main-process console so it isn't lost entirely.
function handle(channel, fn) {
  ipcMain.handle(channel, async (event, payload) => {
    try {
      return await fn(event, payload);
    } catch (e) {
      // login_timeout, not_authenticated... are thrown as a plain Error(code): their message is the code.
      const code = e.code || (Object.prototype.hasOwnProperty.call(ERROR_MESSAGES, e.message) ? e.message : `${channel.replace(/[:.]/g, "_")}_error`);
      console.error(`ipc ${channel} failed:`, e);
      return fail(code, humanMessage(code, e.message));
    }
  });
}

handle("runtime:status", () => {
  const cmd = resolveRuntimeCommand();
  return cmd ? { ok: true, command: cmd.command } : fail("runtime_missing");
});

handle("skills:list", () => mcp.listSkills());

handle("skills:inputs", (_e, payload) => mcp.getSkillInputs(payload.skill, payload.workspace_id));

handle("skills:execute", async (_e, payload) => {
  const startedAt = Date.now();
  const result = await mcp.executeSkill(payload);
  history.pushHistory({
    at: new Date(startedAt).toISOString(),
    duration_ms: Date.now() - startedAt,
    via: "form",
    skill: payload.skill,
    workspace_id: payload.workspace_id || null,
    status: result.status,
    run_id: result.run_id,
    message: String(result.text || "").trim().slice(0, 300),
  });
  return result;
});

ipcMain.handle("history:list", () => ({ ok: true, items: history.loadHistory() }));

handle("history:delete", (_e, payload) => ({ ok: true, items: history.deleteHistoryEntry(payload.at) }));

ipcMain.handle("settings:get", () => ({ ok: true, ...settings.publicSettings() }));

handle("settings:save", (_e, payload) => ({ ok: true, ...settings.saveSettings(payload || {}) }));

handle("sessions:list", async () => ({ ok: true, sessions: await sessionsStore.listSessions() }));

handle("sessions:create", async () => ({ ok: true, session: await sessionsStore.createSession() }));

handle("sessions:load", async (_e, payload) => ({ ok: true, session: await sessionsStore.loadSession(payload.id) }));

handle("sessions:delete", async (_e, payload) => {
  await sessionsStore.deleteSession(payload.id);
  return { ok: true };
});

handle("sessions:rename", async (_e, payload) => ({ ok: true, title: await sessionsStore.renameSession(payload.id, payload.title) }));

handle("sessions:search", async (_e, payload) => ({ ok: true, results: await sessionsStore.searchSessions(payload && payload.query) }));

// AUTH-26: chats, history and settings follow the signed-in person. When the person changes, reload the window
// so nothing from the previous one stays on screen.
function followAccount(identity) {
  if (account.apply(identity) && mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.reload();
}

handle("auth:login", async () => {
  const identity = await authService.login();
  followAccount(identity);
  return { ok: true, identity };
});
handle("auth:cancel", () => { authService.cancelLogin(); return { ok: true }; });

ipcMain.handle("auth:logout", () => {
  authService.logout();
  followAccount(null);
  return { ok: true };
});

ipcMain.handle("auth:status", async () => {
  const identity = await authService.currentIdentity();
  followAccount(identity);
  return { ok: true, signedIn: Boolean(identity), identity };
});

handle("account:contexts", async () => ({ ok: true, ...(await executeClient.getContexts()) }));

handle("account:set-context", (_e, payload) => {
  const workspaceId = String(payload?.workspaceId || "").trim();
  if (!workspaceId) return fail("invalid_workspace");
  return { ok: true, ...settings.saveSettings({ activeWorkspaceId: workspaceId }) };
});

ipcMain.handle("shell:openExternal", (_e, payload) => {
  shell.openExternal(String(payload?.url || ""));
  return { ok: true };
});

ipcMain.handle("window:minimize", (event) => {
  windowFromEvent(event)?.minimize();
});

ipcMain.handle("window:toggle-maximize", (event) => {
  const win = windowFromEvent(event);
  if (!win) return false;
  if (win.isMaximized()) win.unmaximize();
  else win.maximize();
  return win.isMaximized();
});

ipcMain.handle("window:close", (event) => {
  windowFromEvent(event)?.close();
});

ipcMain.handle("window:is-maximized", (event) => Boolean(windowFromEvent(event)?.isMaximized()));

// The renderer draws the tab strip itself (real DOM — it has to sit outside whatever rect
// gets reported here, since a WebContentsView floats above regular page content and would
// occlude it) and reports only the empty rect below it. `rect` is in window-local, unscaled
// pixels — exactly what WebContentsView.setBounds expects — via a ResizeObserver on that
// placeholder element.
ipcMain.handle("panel:bounds", (_e, payload) => {
  const { runId, tabId, rect } = payload || {};
  if (!runId || !tabId || !rect) return { ok: false };
  browserPanel.setActiveBounds(runId, tabId, rect);
  return { ok: true };
});

// The renderer switched chats (or started a new one): hide the panels of the other chats.
ipcMain.handle("panel:set-chat", (_e, payload) => {
  browserPanel.setVisibleChat((payload && payload.chatId) || null);
  return { ok: true };
});

// The title-bar "Browser" toggle hid the panel: park every native view.
ipcMain.handle("panel:hide-all", () => {
  browserPanel.hideAll();
  return { ok: true };
});

handle("panel:capture", async () => ({ ok: true, dataUrl: await browserPanel.captureVisible() }));

// "Take a screenshot": step the window out of the way, grab the primary screen, bring it back.
handle("screen:capture", async (event) => {
  const win = windowFromEvent(event);
  const display = screen.getPrimaryDisplay();
  const size = {
    width: Math.round(display.size.width * display.scaleFactor),
    height: Math.round(display.size.height * display.scaleFactor),
  };
  win?.hide();
  try {
    await new Promise((r) => setTimeout(r, 250)); // let the compositor drop the window first
    const sources = await desktopCapturer.getSources({ types: ["screen"], thumbnailSize: size });
    const src = sources.find((s) => s.display_id === String(display.id)) || sources[0];
    if (!src) throw new Error("No screen to capture.");
    return { ok: true, dataUrl: `data:image/jpeg;base64,${src.thumbnail.toJPEG(85).toString("base64")}` };
  } finally {
    win?.show();
  }
});

ipcMain.handle("panel:select-tab", (_e, payload) => {
  const { runId, tabId, rect } = payload || {};
  if (!runId || !tabId) return { ok: false };
  // Reuses setActiveBounds for the switch itself — selecting a tab and placing it are the
  // same operation (show this one at `rect`, hide every sibling), so there is no separate
  // "just make it active" path to keep in sync with the bounds one.
  browserPanel.setActiveBounds(runId, tabId, rect || { x: 0, y: 0, width: 0, height: 0 });
  return { ok: true };
});

ipcMain.handle("panel:close-tab", async (_e, payload) => {
  const { runId, tabId } = payload || {};
  if (!runId || !tabId) return { ok: false };
  await browserPanel.closeTab(runId, tabId);
  return { ok: true };
});

ipcMain.handle("panel:navigate", (_e, payload) => {
  const { runId, tabId, action, url } = payload || {};
  if (!runId || !tabId) return { ok: false };
  return { ok: browserPanel.navigate(runId, tabId, { action, url }) };
});

ipcMain.handle("panel:new-tab", (_e, payload) => {
  const { runId } = payload || {};
  if (!runId) return { ok: false };
  browserPanel.newTab(runId);
  return { ok: true };
});

// AUTH-6: the renderer's "I'm done signing in" button. browser_panel.js looks up the tab's own
// stored loginKey server-side — the renderer only ever knows a boolean (PanelTab.isLogin), never
// the key itself.
ipcMain.handle("panel:login-done", (_e, payload) => {
  const { runId, tabId } = payload || {};
  if (!runId || !tabId) return { ok: false };
  return browserPanel.loginDone(runId, tabId);
});

const SYSTEM_PROMPT = `You are CONXA. You run recorded Conxa skills on this machine, but only when the user asks you to.

Rules:
- Write plain text. No markdown except **bold**; when listing a skill's inputs use one short line each, like "files: comma-separated filenames to download".
- If the user is greeting you, chatting, or asking a question, just answer. Do not call any tool.
- Only when the user asks to run or automate something: use list_skills to find the skill, get_skill_inputs to see what it needs, then execute_skill. Never call execute_skill until the user has said which task they want and you have every required input from them.
- Fill declared skill inputs from the user. Do not invent secret values.
- A skill runs one record per call. If the user pastes several records (a table or list) and asks to run a skill for each, map their fields to the skill's declared inputs, show the mapping and the record count and wait for a go-ahead, then call execute_skill once per record, one at a time, and end with a per-record result. You cannot open attached files: if the user mentions a spreadsheet or file, ask them to paste its rows into the chat.
- If a run fails, read the failure text. Recovery (self-heal) happens inside the skill runtime; you may retry execute_skill with resume_from / step_overrides only when the failure text asks for them.
- Do not run a shell, edit files, or browse the web yourself. There is no bash or write tool.
- execute_skill already opens a visible browser (watch true) — it renders in this app's own browser panel, not a separate window.
- If execute_skill says "Authentication required" (an application needs sign-in), the sign-in tab(s) are already open in the browser panel and the workflow starts BY ITSELF once the user has signed in. Tell the user which application(s) to sign in to and do NOT call execute_skill again. Do NOT keep polling get_execution_status: the result is posted to this chat automatically when the run ends. Only call it if the user asks for the status.
- To get sign-in out of the way before a run, call authenticate for that skill: it opens the sign-in tab(s) and waits while the user signs in. If it returns status "waiting", tell the user to finish signing in and call authenticate again; when it returns "signed_in", call execute_skill. Never ask the user for their password.`;

// A chat run never starts on the model's say-so alone: the renderer shows a confirm card and
// the tool call waits here for the answer. The form path has its own explicit Run button, so
// only chat goes through this.
const pendingConfirms = new Map(); // confirmId -> resolve(boolean)

function confirmRun(requestId, args) {
  if (settings.loadSettings().runPermission === "auto") return Promise.resolve(true);
  return new Promise((resolve) => {
    if (!mainWindow || mainWindow.isDestroyed()) return resolve(false);
    const id = crypto.randomUUID();
    pendingConfirms.set(id, resolve);
    mainWindow.webContents.send("chat:confirm-run", {
      id,
      requestId,
      skill: args && args.skill,
      inputs: args && args.inputs && typeof args.inputs === "object" ? args.inputs : {},
    });
  });
}

function settleConfirms(approved) {
  for (const resolve of pendingConfirms.values()) resolve(approved);
  pendingConfirms.clear();
}

handle("chat:confirm-run-reply", (_e, payload) => {
  const resolve = pendingConfirms.get(payload && payload.id);
  if (!resolve) return { ok: false };
  pendingConfirms.delete(payload.id);
  resolve(Boolean(payload.approved));
  return { ok: true };
});

// Text files are inlined as fenced blocks; images become OpenAI image_url parts
// (content stays a plain string when there are no images).
function userMessage(text, attachments) {
  const files = Array.isArray(attachments) ? attachments : [];
  const body = [String(text || ""), ...files.filter((a) => a.kind === "text")
    .map((a) => `\n\n[Attached file: ${a.name}]\n\`\`\`\n${a.data}\n\`\`\``)].join("");
  const images = files.filter((a) => a.kind === "image");
  if (!images.length) return { role: "user", content: body };
  return {
    role: "user",
    content: [{ type: "text", text: body }, ...images.map((a) => ({ type: "image_url", image_url: { url: a.data } }))],
  };
}

// One writer per chat session: a background follow-up and a user turn both load → run → save the
// same transcript, so they take turns instead of overwriting each other.
const sessionLocks = new Map(); // sessionId -> tail promise
function withSessionLock(sessionId, fn) {
  const run = (sessionLocks.get(sessionId) || Promise.resolve()).then(fn, fn);
  const tail = run.catch(() => {});
  sessionLocks.set(sessionId, tail);
  tail.then(() => { if (sessionLocks.get(sessionId) === tail) sessionLocks.delete(sessionId); });
  return run;
}

// A chat run that detached to wait for sign-in ends long after its turn did. Follow it, then wake
// the model once (no tools, so it cannot re-run anything) to tell the user how it went.
const following = new Set(); // runIds
async function followRun(sessionId, runId, skill) {
  if (following.has(runId)) return;
  following.add(runId);
  try {
    const st = await waitForRunEnd(runId, mcp.callTool);
    const state = st.state === "unknown" ? "failed" : st.state;
    const summary = String(st.summary || "").trim();
    const started = Date.parse((history.loadHistory().find((e) => e.run_id === runId) || {}).at);
    history.updateRun(runId, {
      status: state,
      message: summary.slice(0, 300),
      duration_ms: Number.isNaN(started) ? null : Date.now() - started,
    });
    await withSessionLock(sessionId, async () => {
      let stored;
      try {
        stored = (await sessionsStore.loadSession(sessionId)).messages;
      } catch {
        return; // the chat was deleted while the run was waiting
      }
      const note = { role: "user", content: `${FOLLOW_UP_PREFIX} The "${skill}" run (run_id ${runId}) that was waiting for sign-in has ended. Status: ${state}. Result: ${summary || "(none)"}. Tell the user the outcome in one or two plain sentences. Do not run it again.` };
      const messages = [...stored, note];
      const full = settings.loadSettings();
      const r = await runTurn({
        model: "conxa-execute",
        system: SYSTEM_PROMPT,
        messages,
        tools: [],
        chatCompletion: executeClient.makeChatCompletion({ targetWorkspaceId: full.activeWorkspaceId, sessionId }),
      });
      const tail = r.ok
        ? r.messages.slice(messages.length + 1) // runTurn prepends the system message
        : [{ role: "assistant", content: state === "completed" ? `${skill} finished.` : `${skill} ${state}: ${summary || "no details."}` }];
      await sessionsStore.saveSessionMessages(sessionId, [...messages, ...tail]);
    });
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send("chat:session-updated", { sessionId });
  } catch (e) {
    console.error("followRun failed:", e);
  } finally {
    following.delete(runId);
  }
}

handle("chat:send", (_e, payload) => withSessionLock(payload && payload.sessionId, () => chatSend(payload)));

async function chatSend(payload) {
  const full = settings.loadSettings();
  const sessionId = payload.sessionId;
  if (!sessionId) return fail("no_session");
  if (!resolveRuntimeCommand()) return fail("runtime_missing");

  const stored = (await sessionsStore.loadSession(sessionId)).messages;
  const editIndex = Number.isInteger(payload.editIndex) && payload.editIndex >= 0 && payload.editIndex <= stored.length
    ? payload.editIndex
    : null;
  const priorMessages = editIndex !== null ? stored.slice(0, editIndex) : stored;
  const nextMessages = compaction.pruneIfNeeded([
    ...priorMessages,
    userMessage(payload.text, payload.attachments),
  ]);

  const detached = new Map(); // runId -> skill: runs parked on sign-in that this turn did not see end
  const tools = await mcp.listChatTools();
  const requestId = payload.requestId;
  let reasoning = "";
  const onDelta = requestId
    ? (chunk) => {
        if (chunk.type === "reasoning") reasoning += chunk.text || "";
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send("chat:delta", { requestId, ...chunk });
        }
      }
    : undefined;
  const result = await runTurn({
    model: "conxa-execute", // advisory only — the proxy picks the real provider/model per plan.
    system: SYSTEM_PROMPT,
    messages: nextMessages,
    tools,
    chatCompletion: executeClient.makeChatCompletion({ targetWorkspaceId: full.activeWorkspaceId, sessionId, onDelta }),
    executeTool: async (name, args) => {
      if (name === "execute_skill") {
        if (!(await confirmRun(requestId, args))) {
          return "The user declined to run this skill. Do not retry it; ask what they would like instead.";
        }
        // Pick the run id here so the browser panel knows which chat owns the run's views.
        const runId = `r_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
        browserPanel.tagRun(runId, sessionId);
        args = { ...args, watch: true, _run_id: runId };
      }
      const startedAt = Date.now();
      const text = await mcp.callTool(name, args);
      if (name === "get_execution_status" && args && args.run_id) {
        // The model already read how this run ended, so no follow-up is needed.
        try { if (TERMINAL.has(JSON.parse(text).state)) detached.delete(String(args.run_id)); } catch { /* not JSON */ }
      }
      if (name === "execute_skill") {
        const runId = extractRunId(text);
        if (classifyRunResult(text) === "awaiting_auth" && runId) detached.set(runId, args.skill);
        history.pushHistory({
          at: new Date(startedAt).toISOString(),
          // A run parked on sign-in has no real duration yet — followRun fills it in when it ends.
          duration_ms: classifyRunResult(text) === "awaiting_auth" ? null : Date.now() - startedAt,
          skill: args.skill,
          workspace_id: args.workspace_id || null,
          status: classifyRunResult(text),
          run_id: extractRunId(text),
          message: String(text || "").trim().slice(0, 300),
          via: "chat",
          session_id: sessionId,
        });
      }
      return text;
    },
  });
  if (!result.ok) {
    // The server sometimes answers with a bare code (execute_context_not_available, ...): say it in words.
    const err = String(result.error || "");
    return fail("model_error", /^[a-z][a-z0-9_]*$/.test(err) ? humanMessage(err) : err);
  }

  if (reasoning) {
    const last = result.messages[result.messages.length - 1];
    if (last && last.role === "assistant" && !last.tool_calls) last.thinking = reasoning;
  }
  await sessionsStore.saveSessionMessages(sessionId, result.messages);
  for (const [runId, skill] of detached) followRun(sessionId, runId, skill);
  return { ok: true, text: result.text };
}

// ─── Auto-update ────────────────────────────────────────────────────────────
// autoDownload/autoInstallOnAppQuit stay at electron-updater's defaults (true)
// so the existing silent behavior (background check → download → install on
// quit, no user action needed) is unchanged. These handlers just give the
// Settings screen a way to trigger a check sooner and see progress — they
// don't switch the app to Build Studio's fully-manual model.
let updateListenersRegistered = false;
function sendUpdateStatus(payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("update:status", payload);
  }
}
function ensureUpdateListeners() {
  if (updateListenersRegistered) return;
  updateListenersRegistered = true;
  autoUpdater.logger = null;
  autoUpdater.on("download-progress", (info) => {
    sendUpdateStatus({
      phase: "download-progress",
      percent: info.percent,
      bytesPerSecond: info.bytesPerSecond,
      transferred: info.transferred,
      total: info.total,
    });
  });
  autoUpdater.on("update-downloaded", () => sendUpdateStatus({ phase: "downloaded" }));
  autoUpdater.on("error", (err) => {
    console.error("autoUpdater error:", err);
    sendUpdateStatus({ phase: "error", message: err.message });
  });
}
// Minimal semver greater-than without an extra dependency.
function semverGt(a, b) {
  const pa = a.replace(/^v/, "").split(".").map(Number);
  const pb = b.replace(/^v/, "").split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    if ((pa[i] || 0) > (pb[i] || 0)) return true;
    if ((pa[i] || 0) < (pb[i] || 0)) return false;
  }
  return false;
}

ipcMain.handle("app:version", () => app.getVersion());

// Fail-open: any error returns { available:false, error } so the Settings screen
// never throws on a broken network. In dev, checkForUpdates() returns null (not
// packaged) — short-circuit early to be explicit, same as Build Studio.
ipcMain.handle("update:check", async () => {
  const currentVersion = app.getVersion();
  if (IS_DEV) {
    return { available: false, currentVersion };
  }
  try {
    const base = executeClient.baseUrl();
    const res = await fetch(`${base}/api/v1/updates/execute-manifest`, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) {
      return { available: false, currentVersion, error: `Manifest fetch failed: HTTP ${res.status}` };
    }
    const manifest = await res.json();
    if (!manifest.win_url) {
      return { available: false, currentVersion, error: "Manifest has no win_url" };
    }
    ensureUpdateListeners();
    autoUpdater.setFeedURL({ provider: "generic", url: `${base}/api/v1/updates/execute/`, useMultipleRangeRequest: false });
    const r = await autoUpdater.checkForUpdates();
    if (r === null) {
      return { available: false, currentVersion };
    }
    const latestVersion = String(r.updateInfo.version || "").replace(/^v/i, "");
    return { available: semverGt(latestVersion, currentVersion), currentVersion, latestVersion };
  } catch (err) {
    return { available: false, currentVersion, error: err.message };
  }
});

// Only meaningful once a "downloaded" status has fired — lets the user install
// immediately instead of waiting for the automatic install-on-quit.
ipcMain.handle("update:install", () => {
  autoUpdater.quitAndInstall(true /* isSilent */, true /* isForceRunAfter */);
});

app.on("second-instance", () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);
  await _cdpPortReady; // must land before this point regardless — see _freePort's comment
  // The runtime (spawned lazily by mcp_client.js's ensureEngine, which forwards this
  // process's whole process.env) picks these two up with no further plumbing needed —
  // see host_browser.js on the runtime side for what each one is for. Set before
  // createWindow() so they're already present by the time anything can execute a skill.
  const { url: controlUrl } = await browserControl.start();
  process.env.CONXA_HOST_BROWSER_CDP = `http://127.0.0.1:${_CDP_PORT}`;
  process.env.CONXA_HOST_CONTROL_URL = controlUrl;
  createWindow();

  // Silent auto-update: feed URL + useMultipleRangeRequest come from app-update.yml
  // (generated by electron-builder from electron-builder.yml's publish block). No
  // update UI is required for this to work — autoDownload/autoInstallOnAppQuit
  // default true, so the installer just runs the next time the user quits.
  // ensureUpdateListeners() also forwards progress to the Settings screen, in case
  // it's open when this background check happens to find and download an update.
  // Failures are logged, not surfaced to the user — a failed background update
  // check should never interrupt them — but must not vanish entirely, or a
  // persistently broken updater is undiagnosable.
  if (!IS_DEV) {
    ensureUpdateListeners();
    autoUpdater.checkForUpdates().catch((err) => console.error("autoUpdater check failed:", err));
  }
});

app.on("window-all-closed", () => {
  browserControl.stop();
  mcp.stopEngine().finally(() => app.quit());
});
