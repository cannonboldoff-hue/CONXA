import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Attachment, ChatMessage, ConfirmRun, ExecuteContext, HistoryRow, Identity, SessionSummary, SkillRow } from "./bridge";
import { SettingsModal } from "./SettingsModal";
import { TitleBar } from "./TitleBar";
import { BrowserPanel } from "./BrowserPanel";
import { Sidebar, type View } from "./Sidebar";
import { Composer } from "./Composer";
import { RunCard } from "./RunCard";
import { RunsPage, SearchPage, SkillsPage } from "./Pages";
import { BrandMark, Button, Icon, MsgActions, paths } from "./ui";

function statusLabel(status: string) {
  if (status === "completed") return "Done";
  if (status === "busy") return "Busy";
  if (status === "awaiting_auth") return "Waiting for sign-in";
  return "Failed";
}

function userDisplayName(identity: Identity | null, signedIn: boolean) {
  const full = [identity?.first_name, identity?.last_name].filter(Boolean).join(" ").trim();
  if (full) return full;
  if (identity?.name?.trim()) return identity.name.trim();
  if (identity?.email?.trim()) return identity.email.split("@")[0];
  return signedIn ? "Account" : "Guest";
}

function userInitials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

function greeting(name: string) {
  const h = new Date().getHours();
  const part = h < 12 ? "morning" : h < 18 ? "afternoon" : "evening";
  const first = name === "Account" ? "" : name.split(/\s+/)[0];
  return first ? `Good ${part}, ${first}` : `Good ${part}`;
}

type Field = {
  name: string;
  description: string;
  required: boolean;
  options?: string[]; // select (enum) or multiselect (array of enum'd items)
  columns?: string[]; // a table input: one row per repeat of a loop, a text value for each column
  multi: boolean;
  date: boolean;
  secret: boolean;
  default: string | string[];
};

// get_skill_inputs returns the compiler's shape: { inputs: [{ name, type, description, enum, items,
// format, default, optional, sensitive }] }. Required = not optional and no default (server.js gate).
function fieldList(schema: Record<string, unknown>): Field[] {
  const raw = Array.isArray(schema.inputs) ? (schema.inputs as Record<string, any>[]) : [];
  return raw.filter((f) => f && f.name).map((f) => {
    const columns: string[] | undefined = f.type === "array" && Array.isArray(f.columns) && f.columns.length ? f.columns.map(String) : undefined;
    const multi = f.type === "array" && !columns;
    const options: string[] | undefined = multi ? f.items?.enum : f.enum;
    const hasDefault = f.default !== undefined && f.default !== null && String(f.default).trim() !== "";
    return {
      name: String(f.name),
      description: String(f.description || ""),
      required: !f.optional && !hasDefault,
      options: Array.isArray(options) && options.length ? options : undefined,
      columns,
      multi,
      date: f.format === "date",
      secret: Boolean(f.sensitive),
      default: columns ? [] : hasDefault ? (multi && !Array.isArray(f.default) ? String(f.default).split(",").map((s) => s.trim()) : f.default) : multi ? [] : "",
    };
  });
}

const inputCls = "mt-1.5 h-11 w-full rounded-xl border border-line bg-bg-elevated px-3.5 text-[14.5px] outline-none transition-colors focus:border-brand/50";

// Confirm card shows what is about to run, but never echoes a secret back onto the screen.
const SECRET_NAME = /pass|secret|token|key|pin\b/i;

function shownInput(name: string, value: unknown) {
  return SECRET_NAME.test(name) ? "••••••" : String(value ?? "");
}

// CONXA's replies use plain **bold** markdown; render it instead of showing the literal asterisks.
function renderBold(text: string) {
  const parts = text.replace(/^([ \t]*)[-*] /gm, "$1• ").split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={i} className="font-semibold text-fg">{part.slice(2, -2)}</strong>
    ) : (
      part
    )
  );
}

type LiveTurn = { user: ChatMessage; editIndex: number | null; content: string; thinking: string };
const MAX_ATTACH_BYTES = 10 * 1024 * 1024;
const TEXT_EXT = /\.(txt|md|csv|tsv|json|jsonl|ya?ml|xml|html?|css|log|py|js|jsx|ts|tsx|java|c|cpp|h|go|rs|rb|php|sh|sql|toml|ini)$/i;
const RUN_UPDATE = "[Conxa run update]";

function msgText(m: ChatMessage): string {
  if (m.content == null) return "";
  return typeof m.content === "string" ? m.content : m.content.map((p) => (p.type === "text" ? p.text : "")).join("");
}
function msgImages(m: ChatMessage): string[] {
  return typeof m.content === "string" || m.content == null ? [] : m.content.flatMap((p) => (p.type === "image_url" ? [p.image_url.url] : []));
}

// What the thread shows: the user's and assistant's words, plus one card per skill run.
type ThreadItem =
  | { kind: "user" | "assistant"; m: ChatMessage & { error?: boolean }; idx: number }
  | { kind: "run"; key: string; skill: string; result: string };

function threadItems(log: (ChatMessage & { error?: boolean })[]): ThreadItem[] {
  const toolResults = new Map(log.filter((m) => m.role === "tool" && m.tool_call_id).map((m) => [m.tool_call_id!, msgText(m)]));
  const items: ThreadItem[] = [];
  log.forEach((m, idx) => {
    const text = msgText(m);
    if (m.role === "user") {
      if (!text.startsWith(RUN_UPDATE) && (text || msgImages(m).length)) items.push({ kind: "user", m, idx });
      return;
    }
    if (m.role !== "assistant") return;
    if (text) items.push({ kind: "assistant", m, idx });
    for (const call of m.tool_calls || []) {
      const result = toolResults.get(call.id);
      if (call.function?.name !== "execute_skill" || result === undefined) continue;
      let skill = "";
      try { skill = String(JSON.parse(call.function.arguments || "{}").skill || ""); } catch { /* malformed args */ }
      items.push({ kind: "run", key: call.id, skill, result });
    }
  });
  return items;
}

export function App() {
  const api = window.conxaExecute;
  const [runtimeOk, setRuntimeOk] = useState<boolean | null>(null);
  const [runtimeMsg, setRuntimeMsg] = useState("");
  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [selected, setSelected] = useState<SkillRow | null>(null);
  const [schema, setSchema] = useState<Record<string, unknown>>({});
  const [inputsError, setInputsError] = useState("");
  const [values, setValues] = useState<Record<string, string | string[] | Record<string, string>[]>>({});
  const [busy, setBusy] = useState(false);
  const [formMsg, setFormMsg] = useState("");
  const [formOk, setFormOk] = useState(true);
  const [formDetail, setFormDetail] = useState("");
  const [formDetailsOpen, setFormDetailsOpen] = useState(false);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [view, setView] = useState<View>("chat");
  const [showSettings, setShowSettings] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [chatLog, setChatLog] = useState<(ChatMessage & { error?: boolean })[]>([]);
  // In-flight turns, keyed by chat. The main process only saves a transcript when its turn ends,
  // so the sent message and streamed text live here until then (lets you switch chats mid-run).
  const [live, setLive] = useState<Record<string, LiveTurn>>({});
  const [chatErrors, setChatErrors] = useState<Record<string, string>>({});
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attachError, setAttachError] = useState("");
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const [pendingRun, setPendingRun] = useState<ConfirmRun | null>(null);
  const [runPermission, setRunPermission] = useState<"ask" | "auto">("ask");
  const [openThinking, setOpenThinking] = useState<Set<number>>(new Set());
  const [chatInput, setChatInput] = useState("");
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);

  // Back/forward history of (view, chat, selected skill). Recorded by watching state,
  // so every navigation path is covered; navGo moves navPos first so replays aren't re-pushed.
  const navHist = useRef<{ view: View; sessionId: string | null; selected: SkillRow | null }[]>([]);
  const navPos = useRef(-1);
  const [navTick, setNavTick] = useState(0);
  useEffect(() => {
    const cur = navHist.current[navPos.current];
    if (cur && cur.view === view && cur.sessionId === sessionId && cur.selected === selected) return;
    navHist.current = [...navHist.current.slice(0, navPos.current + 1), { view, sessionId, selected }];
    navPos.current = navHist.current.length - 1;
    setNavTick((n) => n + 1);
  }, [view, sessionId, selected]);
  void navTick; // re-render so canGoBack/canGoForward refresh
  const sessionRef = useRef<string | null>(null);
  sessionRef.current = sessionId;
  const liveNow = sessionId ? live[sessionId] : undefined;
  const chatBusy = Boolean(liveNow);
  const [signedIn, setSignedIn] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);
  const [startupError, setStartupError] = useState("");
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [contexts, setContexts] = useState<ExecuteContext[]>([]);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState("");
  const [switchingContext, setSwitchingContext] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [signingIn, setSigningIn] = useState(false);
  const [sidebarError, setSidebarError] = useState("");
  const [browserHidden, setBrowserHidden] = useState(false);
  const [browserRuns, setBrowserRuns] = useState(0);
  const [theme, setTheme] = useState<"light" | "dark">(
    () => (localStorage.getItem("conxa-theme") === "light" ? "light" : "dark"),
  );

  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const w = Number(localStorage.getItem("conxa-sidebar-width"));
    return w >= 200 && w <= 480 ? w : 260;
  });
  const [resizing, setResizing] = useState(false);

  function startResize(e: React.PointerEvent) {
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    setResizing(true);
    let w = sidebarWidth;
    const move = (ev: PointerEvent) => {
      w = Math.min(480, Math.max(200, ev.clientX));
      setSidebarWidth(w);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setResizing(false);
      localStorage.setItem("conxa-sidebar-width", String(w));
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  useEffect(() => api.onConfirmRun(setPendingRun), [api]);

  function answerRun(approved: boolean) {
    if (!pendingRun) return;
    api.confirmRunReply({ id: pendingRun.id, approved });
    setPendingRun(null);
  }

  function changeRunPermission(next: "ask" | "auto") {
    setRunPermission(next);
    api.saveSettings({ runPermission: next });
  }

  useEffect(() => {
    document.documentElement.classList.remove("light", "dark");
    document.documentElement.classList.add(theme);
    localStorage.setItem("conxa-theme", theme);
  }, [theme]);

  // A file dropped anywhere outside the chat would make Electron navigate the window to it.
  useEffect(() => {
    const stop = (e: DragEvent) => e.preventDefault();
    window.addEventListener("dragover", stop);
    window.addEventListener("drop", stop);
    return () => {
      window.removeEventListener("dragover", stop);
      window.removeEventListener("drop", stop);
    };
  }, []);

  const refreshHistory = useCallback(async () => {
    const h = await api.history();
    setHistory(h.items || []);
  }, [api]);

  const refreshSessions = useCallback(async () => {
    const r = await api.listSessions();
    const list = r.sessions || [];
    setSessions(list);
    return list;
  }, [api]);

  // A run that was waiting for sign-in finished in the background and main posted its result.
  useEffect(() => api.onSessionUpdated(async ({ sessionId: sid }) => {
    if (sid === sessionRef.current) {
      const loaded = await api.loadSession({ id: sid });
      setChatLog(loaded.session?.messages || []);
    }
    await refreshHistory();
    await refreshSessions();
  }), [api, refreshHistory, refreshSessions]);

  const refreshAccount = useCallback(async () => {
    const status = await api.authStatus();
    setSignedIn(status.signedIn);
    setIdentity(status.identity || null);
    if (!status.signedIn) {
      setContexts([]);
      setActiveWorkspaceId("");
      return;
    }
    const [settingsRes, contextsRes] = await Promise.all([api.getSettings(), api.getContexts()]);
    setRunPermission(settingsRes.runPermission === "auto" ? "auto" : "ask");
    const list = contextsRes.contexts || [];
    setContexts(list);
    const stored = settingsRes.activeWorkspaceId || "";
    const serverDefault = contextsRes.active_workspace_id || "";
    const resolved = list.some((c) => c.workspace_id === stored)
      ? stored
      : list.some((c) => c.workspace_id === serverDefault)
        ? serverDefault
        : list[0]?.workspace_id || "";
    setActiveWorkspaceId(resolved);
    if (resolved && resolved !== stored) await api.setContext({ workspaceId: resolved });
  }, [api]);

  const load = useCallback(async () => {
    setStartupError("");
    try {
      const st = await api.runtimeStatus();
      setRuntimeOk(st.ok);
      setRuntimeMsg(st.ok ? "" : st.message || "Runtime missing");
      await refreshHistory();
      await refreshAccount();

      const list = await refreshSessions();
      const current = list[0] || (await api.createSession()).session;
      if (current) {
        setSessionId(current.id);
        const loaded = await api.loadSession({ id: current.id });
        setChatLog(loaded.session?.messages || []);
      }

      if (!st.ok) {
        setSkills([]);
        return;
      }
      const listed = await api.listSkills();
      if (!listed.ok) {
        setRuntimeOk(false);
        setRuntimeMsg(listed.message || "Could not list skills");
        setSkills([]);
        return;
      }
      setSkills(listed.skills || []);
    } catch (e) {
      // Any failure here used to leave the app permanently showing a blank
      // window (authChecked never became true) — show a real screen instead.
      setStartupError(e instanceof Error ? e.message : "CONXA couldn't start.");
    } finally {
      setAuthChecked(true);
    }
  }, [api, refreshHistory, refreshSessions, refreshAccount]);

  useEffect(() => { load(); }, [load]);

  // Switching chats (or starting a new one) parks the other chats' browser views; they keep running.
  useEffect(() => { void api.panel.setChat({ chatId: sessionId }); }, [api, sessionId]);

  useEffect(() => {
    // The result of one skill's form run must never show under another skill.
    setFormMsg("");
    setFormDetail("");
    setFormOk(true);
    setFormDetailsOpen(false);
    if (!selected) {
      setSchema({});
      setValues({});
      setInputsError("");
      return;
    }
    let cancelled = false;
    setInputsError("");
    api.getInputs({ skill: selected.skill, workspace_id: selected.workspace_id })
      .then((r) => {
        if (cancelled) return;
        if (!r.ok) {
          setInputsError(r.message || "Could not load this skill's inputs.");
          return;
        }
        const sc = (r.schema || {}) as Record<string, unknown>;
        setSchema(sc);
        const fields = fieldList(sc);
        const next: Record<string, string | string[] | Record<string, string>[]> = {};
        for (const f of fields) next[f.name] = f.default;
        setValues(next);
      })
      .catch(() => {
        if (!cancelled) setInputsError("Could not load this skill's inputs.");
      });
    return () => {
      cancelled = true;
    };
  }, [api, selected]);

  const fields = useMemo(() => fieldList(schema), [schema]);
  const items = useMemo(() => threadItems(chatLog), [chatLog]);
  const historyByRun = useMemo(() => new Map(history.filter((h) => h.run_id).map((h) => [h.run_id!, h])), [history]);
  const skillName = useCallback((slug?: string) => skills.find((s) => s.skill === slug)?.name || slug || "Skill", [skills]);
  // Past the sign-in gate below, `signedIn` is always true — chat is ready
  // as soon as an Execute context (personal or team) has resolved.
  const chatReady = Boolean(activeWorkspaceId);
  const activeContext = contexts.find((c) => c.workspace_id === activeWorkspaceId);
  const onChat = view === "chat" && !selected;
  const emptyChatHome = onChat && items.length === 0;
  const displayName = userDisplayName(identity, signedIn);
  const initials = userInitials(displayName);
  const lastUserText = [...items].reverse().find((i) => i.kind === "user");
  const runAgainText = lastUserText && lastUserText.kind === "user" ? msgText(lastUserText.m).trim() : "";
  const lastAssistantIdx = [...items].reverse().find((i) => i.kind === "assistant");
  const currentTitle = onChat && !emptyChatHome ? sessions.find((s) => s.id === sessionId)?.title || null : null;

  async function runForm() {
    if (!selected || busy) return;
    const missing = fields.filter((f) => f.required && !String(values[f.name] ?? "").trim());
    if (missing.length) {
      setFormOk(false);
      setFormDetail("");
      setFormMsg(`Fill in: ${missing.map((f) => f.name).join(", ")}`);
      return;
    }
    setBusy(true);
    setFormMsg("");
    setFormDetail("");
    setFormDetailsOpen(false);
    const r = await api.execute({ skill: selected.skill, workspace_id: selected.workspace_id, inputs: values });
    setBusy(false);
    setFormOk(Boolean(r.ok));
    setFormMsg(!r.ok ? (r.message || "Run failed") : statusLabel(r.status || "failed"));
    setFormDetail(r.ok ? r.text || "" : "");
    await refreshHistory();
  }

  async function addFiles(files: FileList | null) {
    if (!files) return;
    setAttachError("");
    const added: Attachment[] = [];
    for (const f of Array.from(files)) {
      if (f.size > MAX_ATTACH_BYTES) { setAttachError(`${f.name} is larger than 10 MB.`); continue; }
      const isImage = /^image\/(png|jpe?g|gif|webp)$/.test(f.type);
      const isText = f.type.startsWith("text/") || TEXT_EXT.test(f.name);
      if (!isImage && !isText) { setAttachError(`${f.name}: only images and text files are supported.`); continue; }
      const data = await new Promise<string>((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result));
        r.onerror = () => reject(r.error);
        if (isImage) r.readAsDataURL(f); else r.readAsText(f);
      });
      added.push({ name: f.name, mime: f.type, kind: isImage ? "image" : "text", data });
    }
    setAttachments((prev) => [...prev, ...added]);
  }

  async function addCapture(take: () => Promise<{ ok: boolean; dataUrl?: string; message?: string }>, name: string) {
    setAttachError("");
    const r = await take();
    if (!r.ok || !r.dataUrl) { setAttachError(r.message || "Couldn't capture that."); return; }
    setAttachments((prev) => [...prev, { name, mime: "image/jpeg", kind: "image", data: r.dataUrl! }]);
  }

  // `resend` re-runs an earlier request as-is ("Run again"): it leaves the composer alone.
  async function sendChat(resend?: string) {
    const text = (resend ?? chatInput).trim();
    const files = resend === undefined ? attachments : [];
    if ((!text && files.length === 0) || chatBusy || !sessionId) return;
    const sid = sessionId;
    const editIndex = resend === undefined ? editingIndex : null;
    if (resend === undefined) {
      setChatInput("");
      setAttachments([]);
      setAttachError("");
      setEditingIndex(null);
    }
    const shown: ChatMessage["content"] = files.some((a) => a.kind === "image")
      ? [{ type: "text", text }, ...files.filter((a) => a.kind === "image").map((a) => ({ type: "image_url" as const, image_url: { url: a.data } }))]
      : text;
    setChatLog((prev) => [...(editIndex !== null ? prev.slice(0, editIndex) : prev), { role: "user", content: shown }]);
    setLive((prev) => ({ ...prev, [sid]: { user: { role: "user", content: shown }, editIndex, content: "", thinking: "" } }));
    const requestId = crypto.randomUUID();
    const unsubscribe = api.onChatDelta((delta) => {
      if (delta.requestId !== requestId) return;
      setLive((prev) => {
        const base = prev[sid];
        if (!base) return prev;
        return {
          ...prev,
          [sid]: delta.type === "reasoning"
            ? { ...base, thinking: base.thinking + delta.text }
            : { ...base, content: base.content + delta.text },
        };
      });
    });
    try {
      const r = await api.chatSend({ text, sessionId: sid, requestId, editIndex: editIndex ?? undefined, attachments: files });
      if (sessionRef.current === sid) {
        if (r.ok) {
          // Reload from the session store rather than hand-appending — main.js
          // may have pruned/compacted the transcript for this turn, and that's
          // the authoritative post-turn state.
          const loaded = await api.loadSession({ id: sid });
          setChatLog(loaded.session?.messages || []);
        } else {
          setChatLog((prev) => [...prev, { role: "assistant", content: r.message || "Something went wrong.", error: true }]);
        }
      } else if (!r.ok) {
        setChatErrors((prev) => ({ ...prev, [sid]: r.message || "Something went wrong." }));
      }
    } finally {
      unsubscribe();
      setLive((prev) => { const n = { ...prev }; delete n[sid]; return n; });
    }
    await refreshHistory();
    await refreshSessions();
  }

  async function switchContext(workspaceId: string) {
    if (workspaceId === activeWorkspaceId || switchingContext) return;
    setSwitchingContext(true);
    const r = await api.setContext({ workspaceId });
    setSwitchingContext(false);
    if (r.ok) setActiveWorkspaceId(workspaceId);
    else setSidebarError(r.message || "Could not switch context.");
  }

  async function login() {
    setLoginError("");
    setSigningIn(true);
    const r = await api.authLogin();
    if (r.code === "login_cancelled") return; // the person pressed Cancel: back to the plain button, no error
    setSigningIn(false);
    if (r.ok) {
      await refreshAccount();
    } else {
      setLoginError(r.code === "auth_not_configured" ? "Sign-in isn't set up yet. Contact support." : r.message || "Sign-in failed.");
    }
  }

  async function cancelLogin() {
    setSigningIn(false);
    await api.authCancel();
  }

  async function logout() {
    await api.authLogout();
    await refreshAccount();
  }

  async function goHome() {
    setSelected(null);
    setFormMsg("");
    setView("chat");
    if (sessionId && chatLog.length === 0) {
      setEditingIndex(null);
      return;
    }
    const r = await api.createSession();
    if (!r.session) {
      setSidebarError(r.message || "Could not start a new chat.");
      return;
    }
    setSidebarError("");
    await refreshSessions();
    setSessionId(r.session.id);
    setChatLog([]);
    setEditingIndex(null);
  }

  function navGo(delta: -1 | 1) {
    const t = navHist.current[navPos.current + delta];
    if (!t) return;
    navPos.current += delta;
    if (t.sessionId && t.sessionId !== sessionId) {
      const s = sessions.find((x) => x.id === t.sessionId);
      if (s) void pickSession(s);
    }
    setSelected(t.selected);
    setView(t.view);
    setNavTick((n) => n + 1);
  }

  async function pickSession(s: SessionSummary) {
    setSelected(null);
    setView("chat");
    setSessionId(s.id);
    setEditingIndex(null);
    const loaded = await api.loadSession({ id: s.id });
    if (!loaded.ok) {
      setSidebarError(loaded.message || "Could not open that chat.");
      return;
    }
    setSidebarError("");
    const err = chatErrors[s.id];
    if (err) setChatErrors(({ [s.id]: _gone, ...rest }) => rest);
    setChatLog([
      ...withLive(s.id, loaded.session?.messages || []),
      ...(err ? [{ role: "assistant", content: err, error: true }] : []),
    ]);
  }

  function openSessionById(id: string) {
    const s = sessions.find((x) => x.id === id);
    if (s) void pickSession(s);
  }

  function withLive(id: string, stored: ChatMessage[]): ChatMessage[] {
    const turn = live[id];
    if (!turn) return stored;
    return [...(turn.editIndex !== null ? stored.slice(0, turn.editIndex) : stored), turn.user];
  }

  async function askInChat(s: SkillRow) {
    await goHome();
    setChatInput(`Run ${s.name || s.skill}`);
  }

  async function deleteChat(id: string) {
    const del = await api.deleteSession({ id });
    if (!del.ok) {
      setSidebarError(del.message || "Could not delete that chat.");
      return;
    }
    setSidebarError("");
    const list = await refreshSessions();
    if (sessionId === id) {
      const next = list[0] || (await api.createSession()).session;
      if (next) {
        setSessionId(next.id);
        const loaded = await api.loadSession({ id: next.id });
        setChatLog(loaded.session?.messages || []);
      } else {
        setSessionId(null);
        setChatLog([]);
      }
      setEditingIndex(null);
    }
  }

  async function renameChat(title: string) {
    if (!sessionId) return;
    const r = await api.renameSession({ id: sessionId, title });
    if (!r.ok) setSidebarError(r.message || "Could not rename that chat.");
    await refreshSessions();
  }

  // Latest handlers for the one global shortcut listener.
  const keys = useRef({ goHome });
  keys.current = { goHome };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key === ",") {
        e.preventDefault();
        setShowSettings(true);
      } else if (e.key.toLowerCase() === "n") {
        e.preventDefault();
        void keys.current.goHome();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const onDrag = {
    onDragEnter: (e: React.DragEvent) => {
      if (!e.dataTransfer.types.includes("Files")) return;
      dragDepth.current += 1;
      setDragging(true);
    },
    onDragLeave: () => {
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setDragging(false);
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      void addFiles(e.dataTransfer.files);
    },
  };

  const composer = (variant: "home" | "thread") => (
    <Composer
      variant={variant}
      value={chatInput}
      onChange={setChatInput}
      onSend={() => void sendChat()}
      ready={Boolean(runtimeOk) && chatReady}
      busy={chatBusy}
      attachments={attachments}
      attachError={attachError}
      onAddFiles={(f) => void addFiles(f)}
      onRemoveAttachment={(i) => setAttachments((prev) => prev.filter((_, j) => j !== i))}
      onScreenshot={() => void addCapture(api.captureScreen, "screenshot.jpg")}
      onCaptureBrowser={() => void addCapture(api.panel.capture, "browser-panel.jpg")}
      editing={editingIndex !== null}
      onCancelEdit={() => { setEditingIndex(null); setChatInput(""); }}
      mode="chat"
      onMode={(m) => { if (m === "form") setView("skills"); }}
      permission={runPermission}
      onPermission={changeRunPermission}
      contexts={contexts}
      activeWorkspaceId={activeWorkspaceId}
      onSwitchContext={(id) => void switchContext(id)}
      switchingContext={switchingContext}
    />
  );

  if (!authChecked) {
    return <div className="h-full bg-bg" />;
  }

  if (startupError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 bg-bg text-fg">
        <BrandMark size={34} />
        <div className="text-lg font-medium">CONXA couldn't start</div>
        <p className="max-w-sm text-center text-sm text-fg-muted">{startupError}</p>
        <Button onClick={() => { setAuthChecked(false); load(); }}>Retry</Button>
      </div>
    );
  }

  if (!signedIn) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 bg-bg text-fg">
        <BrandMark size={34} />
        <div className="text-[22px] font-medium tracking-[-0.02em]">Sign in to Conxa</div>
        <p className="max-w-xs text-center text-sm text-fg-muted">Sign in with your Conxa account to use Execute.</p>
        <Button className="px-5 py-2.5" onClick={login} disabled={signingIn}>{signingIn ? "Waiting for your browser..." : "Sign in with Conxa"}</Button>
        {signingIn && <button type="button" className="text-sm text-fg-muted underline hover:text-fg" onClick={cancelLogin}>Cancel</button>}
        {loginError && <p className="max-w-xs text-center text-sm text-err">{loginError}</p>}
      </div>
    );
  }

  const sidebarW = collapsed ? 56 : sidebarWidth;

  return (
    <div className="flex h-full flex-col bg-bg text-fg">
      <TitleBar
        leftWidth={sidebarW}
        onBack={() => navGo(-1)}
        onForward={() => navGo(1)}
        canGoBack={navPos.current > 0}
        canGoForward={navPos.current < navHist.current.length - 1}
        onToggleSidebar={() => setCollapsed((v) => !v)}
        title={currentTitle}
        onRename={(t) => void renameChat(t)}
        onDelete={() => sessionId && void deleteChat(sessionId)}
        browserAvailable={browserRuns > 0}
        browserHidden={browserHidden}
        onToggleBrowser={() => setBrowserHidden((v) => !v)}
      />

      <div className="flex min-h-0 flex-1">
        <Sidebar
          width={sidebarW}
          collapsed={collapsed}
          resizing={resizing}
          onResizeStart={startResize}
          view={selected ? "skills" : view}
          onNav={(v) => { setSelected(null); setView(v); if (v === "runs") void refreshHistory(); }}
          onNewChat={() => void goHome()}
          sessions={sessions}
          sessionId={sessionId}
          running={(id) => id in live}
          onPick={(s) => void pickSession(s)}
          onDelete={(id) => void deleteChat(id)}
          error={sidebarError}
          initials={initials}
          displayName={displayName}
          workspaceName={activeContext?.workspace_name || ""}
          onSettings={() => setShowSettings(true)}
        />

        <main className="relative flex min-w-0 flex-1 flex-col" {...(onChat ? onDrag : {})} onDragOver={(e) => e.preventDefault()}>
          {runtimeOk === false && (
            <div className="border-b border-err/30 bg-err-bg px-5 py-2 text-sm text-err-fg">{runtimeMsg}</div>
          )}

          {selected && (
            <div className="theme-scroll min-h-0 flex-1 overflow-auto px-10 py-12">
              <div className="mx-auto w-full max-w-[520px]">
                <h1 className="m-0 text-[26px] font-medium tracking-[-0.02em]">{selected.name || selected.skill}</h1>
                {selected.description && <p className="mt-2 text-[14px] leading-relaxed text-fg-muted">{selected.description}</p>}
                <div className="mt-8 space-y-4">
                  {inputsError && <p className="text-sm text-err">{inputsError}</p>}
                  {!inputsError && fields.map((f) => f.columns ? (
                    <div key={f.name} className="block text-sm">
                      <span className="text-fg-soft">{f.name}{f.required ? " *" : ""}</span>
                      <div className="mt-1.5 space-y-2">
                        {((values[f.name] as Record<string, string>[]) || []).map((row, n) => (
                          <div key={n} className="flex gap-2">
                            {f.columns!.map((c) => (
                              <input key={c} className="h-11 min-w-0 flex-1 rounded-xl border border-line bg-bg-elevated px-3 text-[14.5px] outline-none focus:border-brand/50" placeholder={c} aria-label={`${c}, row ${n + 1}`} value={row[c] ?? ""} disabled={!runtimeOk}
                                onChange={(e) => setValues((v) => ({ ...v, [f.name]: ((v[f.name] as Record<string, string>[]) || []).map((r, k) => (k === n ? { ...r, [c]: e.target.value } : r)) }))} />
                            ))}
                            <button type="button" className="h-11 rounded-xl border border-line px-3 text-sm text-fg-muted hover:text-fg" aria-label={`Remove row ${n + 1}`} disabled={!runtimeOk}
                              onClick={() => setValues((v) => ({ ...v, [f.name]: ((v[f.name] as Record<string, string>[]) || []).filter((_, k) => k !== n) }))}>Remove</button>
                          </div>
                        ))}
                        <button type="button" className="h-9 rounded-xl border border-line px-4 text-sm text-fg-soft hover:text-fg" disabled={!runtimeOk}
                          onClick={() => setValues((v) => ({ ...v, [f.name]: [...((v[f.name] as Record<string, string>[]) || []), Object.fromEntries(f.columns!.map((c) => [c, ""]))] }))}>Add a row</button>
                      </div>
                      {f.description && <span className="mt-1 block text-xs text-fg-dim">{f.description}</span>}
                    </div>
                  ) : (
                    <label key={f.name} className="block text-sm">
                      <span className="text-fg-soft">{f.name}{f.required ? " *" : ""}</span>
                      {f.multi && f.options ? (
                        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1.5">
                          {f.options.map((o) => {
                            const cur = (values[f.name] as string[]) || [];
                            return (
                              <label key={o} className="flex items-center gap-1.5 text-[14px]">
                                <input type="checkbox" checked={cur.includes(o)} disabled={!runtimeOk}
                                  onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.checked ? [...cur, o] : cur.filter((x) => x !== o) }))} />
                                {o}
                              </label>
                            );
                          })}
                        </div>
                      ) : f.options ? (
                        <select className={inputCls} value={String(values[f.name] ?? "")} onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))} disabled={!runtimeOk}>
                          <option value="" />
                          {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                      ) : (
                        <input className={inputCls} type={f.secret ? "password" : f.date ? "date" : "text"} value={String(values[f.name] ?? "")} onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))} disabled={!runtimeOk} />
                      )}
                      {f.description && <span className="mt-1 block text-xs text-fg-dim">{f.description}</span>}
                    </label>
                  ))}
                  {!inputsError && fields.length === 0 && <p className="text-sm text-fg-muted">This skill has no declared inputs.</p>}
                  <button type="button" className="h-10 rounded-[10px] bg-brand px-5 text-sm font-semibold text-bg hover:bg-brand/90 disabled:opacity-40" disabled={!runtimeOk || busy || Boolean(inputsError)} onClick={runForm}>
                    {busy ? "Running…" : "Run"}
                  </button>
                  {formMsg && (
                    <div className="text-sm">
                      <p className={formOk ? "text-fg-muted" : "text-err"}>{formMsg}</p>
                      {formDetail && (
                        <>
                          <button type="button" className="mt-1 text-xs text-fg-dim underline" onClick={() => setFormDetailsOpen((v) => !v)}>
                            {formDetailsOpen ? "Hide details" : "Show details"}
                          </button>
                          {formDetailsOpen && <pre className="mt-1 whitespace-pre-wrap text-xs text-fg-dim">{formDetail}</pre>}
                        </>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {!selected && view === "search" && <SearchPage onOpen={openSessionById} />}
          {!selected && view === "skills" && (
            <SkillsPage skills={skills} runtimeOk={runtimeOk} onRunForm={setSelected} onAskChat={(s) => void askInChat(s)} />
          )}
          {!selected && view === "runs" && (
            <RunsPage
              items={history}
              skillName={skillName}
              canOpenChat={(id) => sessions.some((s) => s.id === id)}
              onOpenChat={openSessionById}
              onDelete={async (at) => { const r = await api.deleteHistory({ at }); setHistory(r.items || []); }}
            />
          )}

          {emptyChatHome && (
            <div className="flex flex-1 flex-col items-center justify-center px-10 pb-20">
              <div className="flex w-full max-w-[720px] flex-col items-center gap-7">
                <div className="flex items-center gap-3.5">
                  <BrandMark size={34} />
                  <h1 className="m-0 text-[34px] font-medium tracking-[-0.025em]">{greeting(displayName)}</h1>
                </div>
                {!chatReady && (
                  <p className="m-0 rounded-xl border border-warn/40 bg-warn-bg px-4 py-2 text-sm text-warn-fg">
                    Waiting for Execute access — forms on the Skills page still run skills without chat.
                  </p>
                )}
                {composer("home")}
                <div className="flex flex-wrap justify-center gap-2">
                  {skills.slice(0, 3).map((s) => (
                    <button
                      key={`${s.workspace_id || ""}/${s.skill}`}
                      type="button"
                      onClick={() => setChatInput(`Run ${s.name || s.skill}`)}
                      className="flex h-[34px] items-center gap-2 rounded-full border border-line px-3.5 text-[13px] text-fg-soft transition-colors hover:border-brand/45 hover:text-fg"
                    >
                      <span className="text-brand"><Icon d={paths.arrowUp} size={14} stroke={1.9} /></span>
                      {s.name || s.skill}
                    </button>
                  ))}
                  <button
                    type="button"
                    onClick={() => setView("skills")}
                    className="flex h-[34px] items-center gap-2 rounded-full border border-line px-3.5 text-[13px] text-fg-soft transition-colors hover:border-brand/45 hover:text-fg"
                  >
                    <span className="text-brand"><Icon d={paths.grid} size={14} stroke={1.9} /></span>
                    Browse all skills
                  </button>
                </div>
              </div>
            </div>
          )}

          {onChat && !emptyChatHome && (
            <div className="flex min-h-0 flex-1 flex-col">
              {!chatReady && (
                <p className="mx-auto mt-4 max-w-[720px] rounded-xl border border-warn/40 bg-warn-bg px-4 py-2 text-sm text-warn-fg">
                  Waiting for Execute access — forms on the Skills page still run skills without chat.
                </p>
              )}
              <div className="theme-scroll flex min-h-0 flex-1 flex-col overflow-auto px-10 pt-8">
                <div className="mx-auto mt-auto flex w-full max-w-[720px] flex-col gap-[22px] pb-7">
                  {items.map((it) => {
                    if (it.kind === "run") {
                      const runId = /\(run_id:\s*([^)]+)\)/.exec(it.result)?.[1].trim();
                      return (
                        <RunCard
                          key={it.key}
                          skillName={skillName(it.skill)}
                          resultText={it.result}
                          entry={runId ? historyByRun.get(runId) : undefined}
                          onShowBrowser={() => setBrowserHidden(false)}
                        />
                      );
                    }
                    const { m, idx } = it;
                    if (it.kind === "user") {
                      const images = msgImages(m);
                      return (
                        <div key={idx} className="group flex flex-col items-end gap-2">
                          {images.length > 0 && (
                            <div className="flex flex-wrap justify-end gap-2">
                              {images.map((u, k) => <img key={k} src={u} alt="" className="h-[120px] w-24 rounded-xl border border-line object-cover" />)}
                            </div>
                          )}
                          {msgText(m) && (
                            <div className="max-w-[520px] whitespace-pre-wrap rounded-[18px] bg-bg-elevated px-4 py-2.5 text-[15px] leading-[1.55]">{msgText(m)}</div>
                          )}
                          <MsgActions text={msgText(m)} onEdit={chatBusy ? undefined : () => { setChatInput(msgText(m)); setEditingIndex(idx); }} />
                        </div>
                      );
                    }
                    const isLast = lastAssistantIdx?.kind === "assistant" && lastAssistantIdx.idx === idx;
                    return (
                        <div key={idx} className="group flex flex-col gap-1.5">
                          {m.thinking && (
                            <button
                              type="button"
                              className="flex w-fit items-center gap-1 text-[12px] text-fg-dim hover:text-fg"
                              aria-expanded={openThinking.has(idx)}
                              onClick={() => setOpenThinking((prev) => {
                                const next = new Set(prev);
                                if (next.has(idx)) next.delete(idx); else next.add(idx);
                                return next;
                              })}
                            >
                              Thinking
                              <Icon d={openThinking.has(idx) ? paths.chevronUp : paths.chevron} size={12} />
                            </button>
                          )}
                          {m.thinking && openThinking.has(idx) && (
                            <pre className="whitespace-pre-wrap border-l-2 border-line pl-3 font-sans text-[13px] leading-relaxed text-fg-dim">{m.thinking}</pre>
                          )}
                          <div className={`whitespace-pre-wrap text-[15.5px] leading-[1.65] ${m.error ? "text-err" : "text-fg/90"}`}>{renderBold(msgText(m))}</div>
                          <div className="-ml-1.5 flex items-center gap-0.5">
                            <MsgActions text={msgText(m)} />
                            {isLast && runAgainText && !chatBusy && (
                              <button
                                type="button"
                                onClick={() => void sendChat(runAgainText)}
                                className="flex h-6 items-center gap-1.5 rounded-md px-2 text-[12.5px] text-fg-dim opacity-0 transition-opacity hover:bg-bg-hover hover:text-fg focus-visible:opacity-100 group-hover:opacity-100"
                              >
                                <Icon d={paths.retry} size={14} /> Run again
                              </button>
                            )}
                          </div>
                        </div>
                    );
                  })}
                  {pendingRun && (
                    <div className="overflow-hidden rounded-[14px] border border-brand/35 bg-bg-sidebar">
                      <div className="flex items-center gap-3 px-4 py-3">
                        <span className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-lg bg-brand/15 text-brand"><Icon d={paths.question} size={15} /></span>
                        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                          <span className="truncate text-[14px] font-medium">{skillName(pendingRun.skill) || "Unnamed skill"}</span>
                          <span className="text-[12px] text-fg-dim">Run this skill on this computer?</span>
                        </span>
                      </div>
                      {Object.keys(pendingRun.inputs).length > 0 && (
                        <dl className="m-0 space-y-1 border-t border-line px-4 py-3 text-[13px]">
                          {Object.entries(pendingRun.inputs).map(([k, v]) => (
                            <div key={k} className="flex gap-3">
                              <dt className="w-32 shrink-0 truncate text-fg-dim">{k}</dt>
                              <dd className="m-0 min-w-0 break-words text-fg-soft">{shownInput(k, v)}</dd>
                            </div>
                          ))}
                        </dl>
                      )}
                      <div className="flex justify-end gap-2 border-t border-line px-3 py-2.5">
                        <button type="button" onClick={() => answerRun(false)} className="h-8 rounded-[9px] border border-line px-3 text-[13px] text-fg-soft hover:bg-bg-hover hover:text-fg">Cancel</button>
                        <button type="button" onClick={() => answerRun(true)} className="h-8 rounded-[9px] bg-brand px-3.5 text-[13px] font-semibold text-bg hover:bg-brand/90">Run</button>
                      </div>
                    </div>
                  )}
                  {liveNow && (
                    <div className="flex flex-col gap-1.5">
                      {liveNow.thinking && (
                        <pre className="whitespace-pre-wrap border-l-2 border-line pl-3 font-sans text-[13px] leading-relaxed text-fg-dim">{liveNow.thinking}</pre>
                      )}
                      {liveNow.content ? (
                        <div className="whitespace-pre-wrap text-[15.5px] leading-[1.65] text-fg/90">{renderBold(liveNow.content)}</div>
                      ) : (
                        !liveNow.thinking && <div className="text-[14px] text-fg-dim">Working…</div>
                      )}
                    </div>
                  )}
                </div>
              </div>
              <div className="flex justify-center px-10 pb-3.5">
                <div className="w-full max-w-[720px]">{composer("thread")}</div>
              </div>
            </div>
          )}

          {dragging && onChat && (
            <div className="pointer-events-none absolute inset-3 z-10 flex flex-col items-center justify-center gap-3.5 rounded-[20px] border-2 border-dashed border-brand/60 bg-bg/90">
              <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-brand/15 text-brand"><Icon d={paths.upload} size={26} /></span>
              <div className="text-[20px] font-medium tracking-[-0.01em]">Drop files to add them to the chat</div>
              <div className="text-[13.5px] text-fg-dim">Images and text files, up to 10 MB each</div>
            </div>
          )}
        </main>

        <BrowserPanel
          sessionId={sessionId}
          hidden={browserHidden}
          onRunCount={setBrowserRuns}
          onWantsShow={() => setBrowserHidden(false)}
        />

        <SettingsModal
          open={showSettings}
          onClose={() => setShowSettings(false)}
          identity={identity}
          contexts={contexts}
          activeWorkspaceId={activeWorkspaceId}
          onSwitchContext={switchContext}
          switchingContext={switchingContext}
          theme={theme}
          onThemeChange={setTheme}
          onLogout={logout}
        />
      </div>
    </div>
  );
}
