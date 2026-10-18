/**
 * One home for the shell's status vocabulary and action-type colours.
 *
 * Deliberately dependency-free (no React, no `@/` imports) so a plain node:test file can
 * exercise it — see electron/test/status.test.mjs. Shape plus colour, never colour alone:
 * the same five glyphs appear in the sidebar, inspector, cards and the group table.
 * Clay orange is never a status colour and never a step-type colour (DESIGN.md).
 */

export type StatusKind = 'ok' | 'warn' | 'fail' | 'run' | 'none'

export const STATUS: Record<StatusKind, { glyph: string; color: string; label: string }> = {
  ok: { glyph: '●', color: '#4ade80', label: 'Ready' },
  warn: { glyph: '▲', color: '#fbbf24', label: 'Needs you' },
  fail: { glyph: '✕', color: '#f87171', label: 'Failed' },
  run: { glyph: '◐', color: '#93b4e6', label: 'In progress' },
  none: { glyph: '○', color: '#8e8e8e', label: 'Not started' },
}

/** Step-type chip colours. Click and select are pink — clay is reserved for the one primary
 * action on screen. */
export const STEP_TYPE_COLORS: Record<string, string> = {
  click: '#e88fb4',
  select: '#e88fb4',
  type: '#5b9bd5',
  navigate: '#9d7cd8',
  scroll: '#8e8e8e',
  hover: '#8e8e8e',
}

export function stepTypeColor(kind: string): string {
  return STEP_TYPE_COLORS[kind.toLowerCase()] ?? STEP_TYPE_COLORS.scroll
}

/** The minimal slice of a workflow the glyph mapping needs (structural, not the API type). */
export type WorkflowGlyphInput = {
  stage: string
  session_id?: string | null
  last_test_status?: 'passed' | 'failed' | 'never' | string
}

export type WorkflowGlyph = { kind: StatusKind; note: string }

/**
 * Interim stage → glyph mapping built on the existing `stage` field. The lifecycle derivation
 * (BUILD-37) replaces this; the glyph set and the notes' tone stay.
 */
export function workflowGlyph(wf: WorkflowGlyphInput): WorkflowGlyph {
  switch (wf.stage) {
    case 'recording':
      return { kind: 'run', note: 'Recording' }
    case 'queued':
      return { kind: 'run', note: 'Waiting to compile' }
    case 'compiling':
      return { kind: 'run', note: 'Compiling' }
    case 'ready_to_compile':
      return wf.session_id
        ? { kind: 'warn', note: 'Recorded · ready to compile' }
        : { kind: 'none', note: 'Not recorded yet' }
    case 'needs_review':
      return { kind: 'warn', note: 'Needs your review' }
    case 'needs_test':
      return wf.last_test_status === 'failed'
        ? { kind: 'fail', note: 'Latest test failed' }
        : { kind: 'warn', note: 'Needs a test' }
    case 'ready':
      return { kind: 'ok', note: '' }
    case 'error':
      return { kind: 'fail', note: 'Something went wrong' }
    default:
      return { kind: 'none', note: '' }
  }
}

/** The worst status among a set, for a group's own glyph: needs-you beats everything quiet. */
export function worstStatus(kinds: StatusKind[]): StatusKind {
  const order: StatusKind[] = ['fail', 'warn', 'run', 'none', 'ok']
  for (const k of order) if (kinds.includes(k)) return k
  return 'none'
}

export type ProgressInput = WorkflowGlyphInput & {
  status?: string
  recording_status?: string | null
  skill_id?: string | null
  compile_status?: string | null
  signed_off?: boolean
}

export type ProgressRow = { label: string; kind: StatusKind; note: string }

/**
 * Interim lifecycle rows for the inspector, derived only from fields that exist today.
 * Clarify and Edit rows join once their backend exists (BUILD-32 / BUILD-37) — a row that
 * could never change would be a fake one.
 */
export function interimProgress(wf: ProgressInput): ProgressRow[] {
  const compiling = wf.stage === 'queued' || wf.stage === 'compiling'
  const recorded = Boolean(wf.session_id)
  const compiled = Boolean(wf.skill_id)
  const compileFailed = wf.recording_status === 'error' || wf.compile_status === 'failed'
  return [
    wf.status === 'needs_auth' && !recorded
      ? { label: 'Sign in', kind: 'warn', note: 'Sign-in needed' }
      : { label: 'Sign in', kind: wf.status === 'error' ? 'fail' : 'ok', note: wf.status === 'error' ? 'Sign-in failed' : '' },
    { label: 'Record', kind: recorded ? 'ok' : 'none', note: recorded ? '' : 'Not recorded yet' },
    compiling
      ? { label: 'Compile', kind: 'run', note: wf.stage === 'queued' ? 'Waiting' : 'Compiling' }
      : compileFailed
        ? { label: 'Compile', kind: 'fail', note: 'Didn’t finish' }
        : { label: 'Compile', kind: compiled ? 'ok' : 'none', note: compiled ? '' : recorded ? 'Ready to compile' : '' },
    {
      label: 'Review',
      kind: !compiled ? 'none' : wf.stage === 'needs_review' ? 'warn' : 'ok',
      note: wf.stage === 'needs_review' ? 'Needs your review' : '',
    },
    {
      label: 'Test',
      kind: wf.last_test_status === 'passed' ? 'ok' : wf.last_test_status === 'failed' ? 'fail' : 'none',
      note: wf.last_test_status === 'passed' ? 'Passed' : wf.last_test_status === 'failed' ? 'Failed' : 'Not tested yet',
    },
    { label: 'Approve', kind: wf.signed_off ? 'ok' : 'none', note: wf.signed_off ? 'Approved' : 'Not approved' },
  ]
}
