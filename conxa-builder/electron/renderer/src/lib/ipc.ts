/** Typed wrapper over the preload `window.conxa` bridge. */

import type { PanelDownload, PanelRunKind, PanelTab } from '@/store/browserStore';

export interface CmdSuccess<T> {
  ok: true;
  result: T;
}
export interface CmdFailure {
  ok: false;
  code: string;
  message: string;
  trace?: string;
}
export type CmdResponse<T> = CmdSuccess<T> | CmdFailure;

export interface BackendEvent {
  type: "event";
  id: string | null;
  phase?: string;
  [k: string]: unknown;
}

export type UpdateStatusEvent =
  | { phase: 'download-progress'; percent: number; bytesPerSecond: number; transferred: number; total: number }
  | { phase: 'downloaded' }
  | { phase: 'error'; message: string }

export interface UpdateCheckResult {
  available: boolean;
  currentVersion: string;
  latestVersion?: string;
  error?: string;
}

declare global {
  interface Window {
    conxa: {
      isPackaged: boolean;
      cmd: <T = unknown>(type: string, payload?: unknown) => Promise<CmdResponse<T>>;
      onEvent: (handler: (event: BackendEvent) => void) => () => void;
      openExternal: (url: string) => Promise<void>;
      pickFile: (opts?: {
        filters?: { name: string; extensions: string[] }[];
        defaultPath?: string;
        multiple?: boolean;
      }) => Promise<string[] | null>;
      saveInstaller: (srcPath: string) => Promise<{ ok: boolean; filePath?: string }>;
      raiseWindow: (raise: boolean) => Promise<void>;
      windowControls: {
        minimize: () => Promise<void>;
        toggleMaximize: () => Promise<boolean>;
        close: () => Promise<void>;
        isMaximized: () => Promise<boolean>;
        onMaximizeChange: (handler: (isMaximized: boolean) => void) => () => void;
      };
      onDeepLink: (handler: (url: string) => void) => () => void;
      /** The local engine (Python backend). */
      engine: {
        status: () => Promise<{ state: 'running' | 'restarting' | 'stopped' }>;
        restart: () => Promise<boolean>;
        diagnostics: () => Promise<string>;
        onStatus: (handler: (msg: { state: 'running' | 'restarting' | 'stopped' }) => void) => () => void;
      };
      /** Embedded test browser (BUILD-42). */
      panel: {
        available: () => Promise<boolean>;
        setBounds: (p: { runId: string; tabId: string; rect: { x: number; y: number; width: number; height: number } }) => Promise<boolean>;
        hide: () => Promise<boolean>;
        selectTab: (p: { runId: string; tabId: string }) => Promise<boolean>;
        /** A tab the person opens (+, Ctrl+T, "Open link in new tab"), blank or on `url`. */
        newTab: (p: { runId: string; url?: string }) => Promise<{ tabId: string }>;
        closeTab: (p: { runId: string; tabId: string }) => Promise<boolean>;
        closeRun: (p: { runId: string }) => Promise<boolean>;
        navigate: (p: { runId: string; tabId: string; action: 'back' | 'forward' | 'reload' | 'hard-reload' | 'stop' | 'load'; url?: string }) => Promise<boolean>;
        downloadAction: (p: { runId: string; id: string; action: 'cancel' | 'retry' | 'show' | 'open' | 'clear' }) => Promise<boolean>;
        onTabs: (handler: (msg: { runId: string; tabs: PanelTab[]; finished?: boolean; closed?: boolean; kind?: PanelRunKind; downloads?: PanelDownload[] }) => void) => () => void;
        onShortcut: (handler: (msg: { runId: string; action: 'focus-address' }) => void) => () => void;
      };
      update: {
        check: () => Promise<UpdateCheckResult>;
        start: () => Promise<void>;
        install: () => Promise<void>;
        getVersion: () => Promise<string>;
        onStatus: (handler: (event: UpdateStatusEvent) => void) => () => void;
      };
    };
  }
}

export class CmdError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

/** Invoke a backend command, throwing CmdError on failure. */
export async function cmd<T = unknown>(type: string, payload?: unknown): Promise<T> {
  const res = await window.conxa.cmd<T>(type, payload);
  if (!res.ok) {
    if (res.trace) console.error(`[${type}] backend error:`, res.code, res.message, "\n", res.trace);
    throw new CmdError(res.code, res.message);
  }
  return res.result;
}
