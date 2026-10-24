import { cmd } from '@/lib/ipc'
import type { ThreadItem } from '@/lib/thread'
import type { StatusKind } from '@/lib/status'

export type LifecycleRow = { label: string; kind: StatusKind; note: string }

export type Lifecycle = {
  stage: 'describe' | 'configure' | 'record' | 'compile' | 'clarify' | 'edit' | 'test' | 'approve'
  substate: string
  glyph: StatusKind
  note: string
  allowed_actions: string[]
  rows?: LifecycleRow[]
}

export type ThreadPage = {
  thread_id: string
  items: ThreadItem[]
  start: number
  total: number
  has_more: boolean
  cursor: number | null
  chat?: Chat | null
  workflow?: { id: string; name: string; group_id: string; group_name: string; skill_id: string | null; target_url: string }
  lifecycle?: Lifecycle
}

export type Chat = { id: string; title: string; created_at: string; updated_at: string }

export type TurnResult = {
  turn_id: string
  status: 'done' | 'cancelled'
  reply: string
  items: ThreadItem[]
  result?: Record<string, unknown>
}

export type CardActionResult = {
  ok?: boolean
  code?: string
  message?: string
  needs?: string[]
  open_form?: { name?: string; group?: string; starting_page?: string; task?: string }
  workflow_id?: string
  start_recording?: boolean
  retry?: boolean
}

export const chatList = () => cmd<{ chats: Chat[] }>('chat_list')
export const chatCreate = (title?: string) => cmd<{ chat: Chat }>('chat_create', { title })
export const chatRename = (chatId: string, title: string) => cmd<{ chat: Chat }>('chat_rename', { chat_id: chatId, title })
export const chatDelete = (chatId: string) => cmd<{ deleted: boolean }>('chat_delete', { chat_id: chatId })

export const threadLoad = (threadId: string, before?: number | null) =>
  cmd<ThreadPage>('thread_load', { thread_id: threadId, before: before ?? undefined })

export const threadTurn = (body: {
  thread_id: string
  message?: string
  turn_id?: string
  anchor?: string
  card_id?: string
  choice?: string
  action?: string
}) => cmd<TurnResult>('thread_turn', body)

export const threadCancel = (turnId: string) => cmd<{ cancelled: boolean }>('thread_cancel', { turn_id: turnId })

export const cardAction = (threadId: string, cardId: string, action: string, payload?: Record<string, unknown>) =>
  cmd<CardActionResult>('card_action', { thread_id: threadId, card_id: cardId, action, payload })

export const getLifecycle = (workflowId: string) => cmd<{ lifecycle: Lifecycle }>('get_lifecycle', { workflow_id: workflowId })
export const listLifecycles = () =>
  cmd<{ lifecycles: Record<string, Omit<Lifecycle, 'rows'>> }>('list_lifecycles')
export const countPublishable = () => cmd<{ count: number }>('count_publishable')

// ── sign-in and recording, driven from the conversation (BUILD-38) ──────────────────────────

export type RecordingRow = {
  sequence: number
  kind: 'click' | 'type' | 'navigate' | 'select' | 'scroll' | 'hover' | 'other'
  text: string
  value: string | null
  can_vary: boolean
  tab: string
}

export type FlowResult = { next?: 'sign_in' | 'record'; card_id?: string; already?: boolean; confirmed?: boolean; reason?: string; session_id?: string }

export const flowBegin = (workflowId: string) => cmd<FlowResult>('flow_begin', { workflow_id: workflowId })
export const flowSignInDone = (workflowId: string, cardId: string, force = false) =>
  cmd<FlowResult>('flow_sign_in_done', { workflow_id: workflowId, card_id: cardId, force })
export const flowSignInCancel = (workflowId: string, cardId: string) =>
  cmd<{ ok: boolean }>('flow_sign_in_cancel', { workflow_id: workflowId, card_id: cardId })
export const flowPauseRecording = (workflowId: string, cardId: string) =>
  cmd<{ ok: boolean }>('flow_pause_recording', { workflow_id: workflowId, card_id: cardId })
export const flowResumeRecording = (workflowId: string, cardId: string) =>
  cmd<{ ok: boolean }>('flow_resume_recording', { workflow_id: workflowId, card_id: cardId })
export const flowFinishRecording = (workflowId: string, cardId: string) =>
  cmd<{ ok: boolean; event_count: number }>('flow_finish_recording', { workflow_id: workflowId, card_id: cardId })
export const flowDiscardRecording = (workflowId: string, cardId: string) =>
  cmd<{ ok: boolean }>('flow_discard_recording', { workflow_id: workflowId, card_id: cardId })
/** "Read this": the next click in the recording browser reads that element's value into an output, instead of clicking it. */
export const flowCaptureValue = (workflowId: string, cardId: string, on = true) =>
  cmd<{ ok: boolean; capturing: boolean }>('flow_capture_value', { workflow_id: workflowId, card_id: cardId, on })
export const flowSetVaries = (sessionId: string, sequence: number, varies: boolean | null) =>
  cmd<{ varies: Record<string, boolean> }>('flow_set_varies', { session_id: sessionId, sequence, varies })
export const recordingRows = (sessionId: string) =>
  cmd<{ rows: RecordingRow[]; varies: Record<string, boolean>; paused: boolean }>('recording_rows', { session_id: sessionId })

// ── compile, Clarify and testing (BUILD-39 / BUILD-32 / BUILD-40) ───────────────────────────

/** A test paused on an expired sign-in: sign in again, then run the same test again by itself. */
export const flowSignInThenTest = (workflowId: string) => cmd<FlowResult>('flow_sign_in_then_test', { workflow_id: workflowId })
export const flowCompile = (workflowId: string) => cmd<{ ok: boolean }>('flow_compile', { workflow_id: workflowId })
export const flowTestPrepare = (workflowId: string) => cmd<{ ok: boolean; card_id?: string }>('flow_test_prepare', { workflow_id: workflowId })
export const flowTestRun = (workflowId: string, cardId: string, inputs: Record<string, string>, guard: boolean, showBrowser: boolean) =>
  cmd<{ ok: boolean }>('flow_test_run', { workflow_id: workflowId, card_id: cardId, inputs, guard, show_browser: showBrowser })
export const flowTestDecision = (workflowId: string, decision: 'run_rest' | 'yes' | 'end_here' | `fork:${string}`) =>
  cmd<{ ok: boolean }>('flow_test_decision', { workflow_id: workflowId, decision })
export const flowTestStop = (workflowId: string) => cmd<{ ok: boolean }>('flow_test_stop', { workflow_id: workflowId })
export const flowApprove = (workflowId: string, cardId: string) =>
  cmd<{ ok: boolean; approved: boolean; built: boolean }>('flow_approve', { workflow_id: workflowId, card_id: cardId })
export const flowAutofixStop = (workflowId: string) => cmd<{ ok: boolean }>('flow_autofix_stop', { workflow_id: workflowId })
export const flowAutofixResume = (workflowId: string) => cmd<{ ok: boolean }>('flow_autofix_resume', { workflow_id: workflowId })

export type StudioSettings = { autofix_enabled: boolean }
export const getStudioSettings = () => cmd<{ settings: StudioSettings }>('get_studio_settings')
export const setStudioSettings = (s: Partial<StudioSettings>) => cmd<{ settings: StudioSettings }>('set_studio_settings', s)

/** Run one of the lifecycle's actions by id (the action strip). Same table and commands chat uses. */
export const flowDoAction = (workflowId: string, action: string, slots?: Record<string, unknown>) =>
  cmd<{ ok: boolean; message?: string }>('flow_do_action', { workflow_id: workflowId, action, slots })
