/// <reference types="vite/client" />

export type SkillRow = {
  skill: string;
  workspace_id?: string;
  name?: string;
  description?: string;
};

export type HistoryRow = {
  at: string;
  skill?: string;
  status: string;
  run_id?: string | null;
  message?: string;
  duration_ms?: number | null;
  via?: "chat" | "form";
  session_id?: string;
};

export type SearchHit = SessionSummary & { snippet: string };

export type ContentPart = { type: "text"; text: string } | { type: "image_url"; image_url: { url: string } };
export type ToolCall = { id: string; type?: string; function: { name: string; arguments: string } };
export type ChatMessage = { role: string; content: string | ContentPart[]; thinking?: string; tool_calls?: ToolCall[]; tool_call_id?: string };
export type Attachment = { name: string; mime: string; kind: "image" | "text"; data: string };

export type ChatDelta = { requestId: string; type: "text" | "reasoning"; text: string };

export type ConfirmRun = { id: string; requestId?: string; skill?: string; inputs: Record<string, unknown> };

export type SessionSummary = { id: string; title: string; createdAt: string; updatedAt: string };

export type SessionDetail = SessionSummary & { messages: ChatMessage[] };

export type Identity = { user_id: string; name?: string; first_name?: string; last_name?: string; email?: string; picture?: string };

export type PanelRect = { x: number; y: number; width: number; height: number };

export type PanelTab = {
  id: string;
  active: boolean;
  label: string;
  url: string;
  canGoBack: boolean;
  canGoForward: boolean;
  loading: boolean;
  // AUTH-6: true only for an actual sign-in tab (never a judge/prover probe tab) — the UI hint
  // for showing an "I'm done signing in" action; the real key stays server-side.
  isLogin: boolean;
};

export type PanelNavAction = "back" | "forward" | "reload" | "stop" | "load";

export type ExecuteContext = {
  workspace_id: string;
  workspace_name: string;
  kind: "member" | "grant";
  credits_remaining?: number | null;
};

export type UpdateCheckResult = { available: boolean; currentVersion: string; latestVersion?: string; error?: string };

export type UpdateStatus =
  | { phase: "download-progress"; percent: number; bytesPerSecond: number; transferred: number; total: number }
  | { phase: "downloaded" }
  | { phase: "error"; message: string };

export type Bridge = {
  runtimeStatus: () => Promise<{ ok: boolean; command?: string; message?: string }>;
  listSkills: () => Promise<{ ok: boolean; skills?: SkillRow[]; message?: string }>;
  getInputs: (p: { skill: string; workspace_id?: string }) => Promise<{ ok: boolean; schema?: Record<string, unknown>; message?: string }>;
  execute: (p: { skill: string; workspace_id?: string; inputs: Record<string, string | string[] | Record<string, string>[]> }) => Promise<{ ok: boolean; status?: string; run_id?: string | null; text?: string; message?: string }>;
  history: () => Promise<{ ok: boolean; items?: HistoryRow[] }>;
  deleteHistory: (p: { at: string }) => Promise<{ ok: boolean; items?: HistoryRow[]; message?: string }>;
  getSettings: () => Promise<{ ok: boolean; activeWorkspaceId?: string; runPermission?: "ask" | "auto"; error?: string }>;
  saveSettings: (p: { activeWorkspaceId?: string; runPermission?: "ask" | "auto" }) => Promise<{ ok: boolean; activeWorkspaceId?: string; runPermission?: "ask" | "auto"; error?: string }>;
  update: {
    check: () => Promise<UpdateCheckResult>;
    install: () => Promise<void>;
    getVersion: () => Promise<string>;
    onStatus: (cb: (p: UpdateStatus) => void) => () => void;
  };
  chatSend: (p: { text: string; sessionId: string; requestId?: string; editIndex?: number; attachments?: Attachment[] }) => Promise<{ ok: boolean; text?: string; message?: string }>;
  onChatDelta: (cb: (p: ChatDelta) => void) => () => void;
  onConfirmRun: (cb: (p: ConfirmRun) => void) => () => void;
  onSessionUpdated: (cb: (p: { sessionId: string }) => void) => () => void;
  confirmRunReply: (p: { id: string; approved: boolean }) => Promise<{ ok: boolean }>;
  listSessions: () => Promise<{ ok: boolean; sessions?: SessionSummary[]; message?: string }>;
  createSession: () => Promise<{ ok: boolean; session?: SessionSummary; message?: string }>;
  loadSession: (p: { id: string }) => Promise<{ ok: boolean; session?: SessionDetail; message?: string }>;
  deleteSession: (p: { id: string }) => Promise<{ ok: boolean; message?: string }>;
  renameSession: (p: { id: string; title: string }) => Promise<{ ok: boolean; title?: string; message?: string }>;
  searchSessions: (p: { query: string }) => Promise<{ ok: boolean; results?: SearchHit[]; message?: string }>;
  captureScreen: () => Promise<{ ok: boolean; dataUrl?: string; message?: string }>;
  authLogin: () => Promise<{ ok: boolean; identity?: Identity; code?: string; message?: string }>;
  authCancel: () => Promise<{ ok: boolean }>;
  authLogout: () => Promise<{ ok: boolean }>;
  authStatus: () => Promise<{ ok: boolean; signedIn: boolean; identity?: Identity | null }>;
  getContexts: () => Promise<{ ok: boolean; contexts?: ExecuteContext[]; active_workspace_id?: string; message?: string }>;
  setContext: (p: { workspaceId: string }) => Promise<{ ok: boolean; activeWorkspaceId?: string; message?: string }>;
  openExternal: (p: { url: string }) => Promise<{ ok: boolean }>;
  panel: {
    setBounds: (p: { runId: string; tabId: string; rect: PanelRect }) => Promise<{ ok: boolean }>;
    setChat: (p: { chatId: string | null }) => Promise<{ ok: boolean }>;
    selectTab: (p: { runId: string; tabId: string; rect?: PanelRect }) => Promise<{ ok: boolean }>;
    closeTab: (p: { runId: string; tabId: string }) => Promise<{ ok: boolean }>;
    navigate: (p: { runId: string; tabId: string; action: PanelNavAction; url?: string }) => Promise<{ ok: boolean }>;
    newTab: (p: { runId: string }) => Promise<{ ok: boolean }>;
    loginDone: (p: { runId: string; tabId: string }) => Promise<{ ok: boolean; error?: string }>;
    hideAll: () => Promise<{ ok: boolean }>;
    capture: () => Promise<{ ok: boolean; dataUrl?: string; message?: string }>;
    onTabsChanged: (cb: (p: { runId: string; tabs: PanelTab[]; chatId?: string | null; focus?: boolean }) => void) => () => void;
  };
  windowControls: {
    minimize: () => Promise<void>;
    toggleMaximize: () => Promise<boolean>;
    close: () => Promise<void>;
    isMaximized: () => Promise<boolean>;
    onMaximizeChange: (cb: (maximized: boolean) => void) => () => void;
  };
};

declare global {
  interface Window {
    conxaExecute: Bridge;
  }
}

export {};
