import { create } from 'zustand'
import { loadLastCopilotSession, type CopilotProposal, type CopilotTurnMessage } from '@/api/workflowApi'

/** Verified retest state (BUILD-26 stage e), seeded once a proposal is accepted. 'confirming'
 *  shows the non-reversible-action count before anything runs; 'running' streams rebuild/retest
 *  log lines; 'done' shows the fixed/still_failing/progressed verdict. Nothing here auto-runs —
 *  every transition out of 'idle' is a deliberate reviewer click. */
export type CopilotVerifyStatus = 'idle' | 'confirming' | 'running' | 'done'

export type CopilotVerifyState = {
  status: CopilotVerifyStatus
  proposalId: string | null
  stepKey: string | null
  /** False until the confirmed=false pre-flight call has actually returned — distinguishes "not
   *  checked yet" from "checked, zero non-reversible steps" so the panel never shows a 0-count
   *  confirm line before the count is real. */
  preflighted: boolean
  irreversibleCount: number
  irreversibleSteps: { step_key: string; description: string }[]
  logs: string[]
  verdict: 'fixed' | 'still_failing' | 'progressed' | null
  message: string
}

const IDLE_VERIFY: CopilotVerifyState = {
  status: 'idle',
  proposalId: null,
  stepKey: null,
  preflighted: false,
  irreversibleCount: 0,
  irreversibleSteps: [],
  logs: [],
  verdict: null,
  message: '',
}

// Matches the launcher button's size-12 (48px) footprint.
const LAUNCHER_SIZE = 48

export type CopilotPosition = { x: number; y: number }

/** Keeps a position (button top-left) fully on-screen while a drag is in progress. */
export function clampPosition(pos: CopilotPosition, size = LAUNCHER_SIZE): CopilotPosition {
  return {
    x: Math.min(Math.max(pos.x, 0), Math.max(0, window.innerWidth - size)),
    y: Math.min(Math.max(pos.y, 0), Math.max(0, window.innerHeight - size)),
  }
}

/** Recomputed from the *current* window size every time it's called. The launcher is not
 *  a remembered, freely-placed widget — it always rests in the bottom-right corner, so
 *  nothing here reads or writes localStorage. */
export function defaultPosition(): CopilotPosition {
  return { x: window.innerWidth - LAUNCHER_SIZE - 20, y: window.innerHeight - LAUNCHER_SIZE - 20 }
}

// One-time cleanup of the now-unused persisted position from before the launcher stopped
// remembering where it was dragged.
try {
  window.localStorage.removeItem('conxa-copilot-position')
} catch {
  /* private-browsing / storage disabled — nothing to clean up */
}

// The Human Review Copilot's conversation state (BUILD-26). Lives outside the launcher/panel
// components so the conversation survives the panel closing and reopening, and so
// WorkflowViewer's step-select handler can guard on a pending proposal — it is a
// "you're about to lose this" state.
type CopilotState = {
  skillId: string | null
  open: boolean
  position: CopilotPosition
  /** True once the reviewer has dragged the launcher this session — until then it keeps
   *  tracking the live bottom-right corner instead of a one-time computed spot. Never
   *  persisted: the launcher always starts back in the corner next time it mounts. */
  positionDragged: boolean
  messages: CopilotTurnMessage[]
  pendingProposal: CopilotProposal | null
  verify: CopilotVerifyState
  sending: boolean
  /** The in-progress assistant reply's text as it streams in — cleared once the turn resolves
   *  and the complete reply lands in `messages` via addMessage. Empty while `sending` is true
   *  and no delta has arrived yet (the panel shows a "thinking" indicator for that gap). */
  streamingText: string
  /** True only while a proposal is pending accept/reject — not while merely chatting. */
  dirty: boolean
  /** BUILD-26 stage g: hydrates from the last archived session for this skill (fire-and-forget
   *  — a resume failure must never block opening the panel) instead of always starting cold. A
   *  skill switch still resets synchronously first, same as before, so the panel never shows the
   *  PREVIOUS skill's conversation while the resume call is in flight. */
  ensureFor: (skillId: string) => void
  setOpen: (open: boolean) => void
  /** In-memory-only move, called on every pointermove while dragging. */
  setPosition: (position: CopilotPosition) => void
  /** Clamps and applies the final position on pointerup — never persisted. The launcher
   *  resets to the bottom-right corner the next time it mounts (see CopilotLauncher). */
  commitPosition: (position: CopilotPosition) => void
  addMessage: (message: CopilotTurnMessage) => void
  /** Drops the user message at `index` and everything after it (so a re-send doesn't pile a
   *  correction on top of the turn it's correcting), and clears any pending proposal since it
   *  would otherwise reference a turn no longer in the transcript. Returns the removed message's
   *  original text so the composer can be re-seeded with it, or null if there was nothing to edit. */
  editFromIndex: (index: number) => string | null
  setPendingProposal: (proposal: CopilotProposal | null) => void
  setSending: (sending: boolean) => void
  appendStreamingDelta: (text: string) => void
  clearStreamingText: () => void
  /** Seeds the verify card right after a proposal is accepted — status starts at 'confirming'
   *  once the pre-flight count is known, not 'running': nothing acts against the real system
   *  until the reviewer clicks through the confirm step. */
  startVerify: (args: { proposalId: string | null; stepKey: string }) => void
  setVerifyConfirming: (irreversibleCount: number, irreversibleSteps: { step_key: string; description: string }[]) => void
  setVerifyRunning: () => void
  appendVerifyLog: (message: string) => void
  setVerifyDone: (verdict: 'fixed' | 'still_failing' | 'progressed' | null, message: string) => void
  clearVerify: () => void
  reset: () => void
}

export const useCopilotStore = create<CopilotState>((set, get) => ({
  skillId: null,
  open: false,
  position: defaultPosition(),
  positionDragged: false,
  messages: [],
  pendingProposal: null,
  verify: IDLE_VERIFY,
  sending: false,
  streamingText: '',
  dirty: false,
  ensureFor: (skillId) => {
    if (get().skillId === skillId) return
    set({ skillId, messages: [], pendingProposal: null, verify: IDLE_VERIFY, sending: false, streamingText: '', dirty: false })
    loadLastCopilotSession(skillId)
      .then((res) => {
        // Skill may have changed again (or the panel reset) before this resolved — never
        // overwrite a newer conversation with a stale resume.
        if (get().skillId !== skillId || get().messages.length > 0) return
        if (res.messages?.length) set({ messages: res.messages })
      })
      .catch(() => {
        /* no archive yet, or a disk hiccup — the panel just starts cold, same as before this existed */
      })
  },
  setOpen: (open) => set({ open }),
  setPosition: (position) => set({ position }),
  commitPosition: (position) => set({ position: clampPosition(position), positionDragged: true }),
  addMessage: (message) => set((s) => ({ messages: [...s.messages, message] })),
  editFromIndex: (index) => {
    const message = get().messages[index]
    if (!message || message.role !== 'user') return null
    set((s) => ({ messages: s.messages.slice(0, index), pendingProposal: null, dirty: false }))
    return message.text
  },
  setPendingProposal: (proposal) => set({ pendingProposal: proposal, dirty: proposal !== null }),
  setSending: (sending) => set({ sending }),
  appendStreamingDelta: (text) => set((s) => ({ streamingText: s.streamingText + text })),
  clearStreamingText: () => set({ streamingText: '' }),
  startVerify: ({ proposalId, stepKey }) =>
    set({ verify: { ...IDLE_VERIFY, status: 'confirming', proposalId, stepKey } }),
  setVerifyConfirming: (irreversibleCount, irreversibleSteps) =>
    set((s) => ({ verify: { ...s.verify, status: 'confirming', preflighted: true, irreversibleCount, irreversibleSteps } })),
  setVerifyRunning: () => set((s) => ({ verify: { ...s.verify, status: 'running', logs: [] } })),
  appendVerifyLog: (message) => set((s) => ({ verify: { ...s.verify, logs: [...s.verify.logs, message] } })),
  setVerifyDone: (verdict, message) =>
    set((s) => ({ verify: { ...s.verify, status: 'done', verdict, message } })),
  clearVerify: () => set({ verify: IDLE_VERIFY }),
  reset: () => set({ messages: [], pendingProposal: null, verify: IDLE_VERIFY, sending: false, streamingText: '', dirty: false }),
}))
