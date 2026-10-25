/**
 * The five stages a workflow conversation walks through, as the person sees them:
 * Record → Compile → Test → Approve → Publish. Pure and dependency-free so node:test can load it
 * (electron/test/stages.test.mjs).
 *
 * The backend's lifecycle has finer stages (describe, configure, clarify, edit…); they fold into
 * these five. Thread items are grouped by the same five so the divider labels and the progress
 * bar always agree.
 */

export const STAGES = ['record', 'compile', 'test', 'approve', 'publish'] as const
export type Stage = (typeof STAGES)[number]

export const STAGE_LABEL: Record<Stage, string> = {
  record: 'Record',
  compile: 'Compile',
  test: 'Test',
  approve: 'Approve',
  publish: 'Publish',
}

/** A lifecycle as the progress bar needs it (see api/conversationApi.ts `Lifecycle`). */
export type LifecycleLike = { stage: string; substate: string }

export function stageOfLifecycle(lc: LifecycleLike): Stage {
  switch (lc.stage) {
    case 'describe':
    case 'configure':
    case 'record':
      return 'record'
    case 'compile':
    case 'clarify':
    case 'edit':
      return 'compile'
    case 'test':
      return 'test'
    case 'approve':
      return lc.substate === 'approved' ? 'publish' : 'approve'
    default:
      return 'record'
  }
}

const CARD_STAGE: Record<string, Stage> = {
  sign_in: 'record',
  recording: 'record',
  record_plan: 'record',
  compile: 'compile',
  clarify_question: 'compile',
  workflow_summary: 'compile',
  fork_variant: 'compile',
  test_inputs: 'test',
  test_run: 'test',
  autofix: 'test',
  pending_change: 'test',
}

/** The stage an item itself names, or null when it just continues whichever stage came before. */
export function stageOfItem(item: { kind: string; card_type?: string; text?: string }): Stage | null {
  if (item.kind === 'card') return (item.card_type && CARD_STAGE[item.card_type]) || null
  if (item.kind === 'event' && /^(Test|Paused|Preview finished)/.test(String(item.text ?? ''))) return 'test'
  return null
}

export type StageGroup<T> = { stage: Stage; items: T[] }

/** Split a thread into consecutive runs by stage. Items that name no stage stay with the run
 *  they follow; the very first run starts in Record. A stage that comes round again (a re-record
 *  after a test) starts a new run rather than merging backwards, so the order stays true to time. */
export function groupByStage<T extends { kind: string; card_type?: string; text?: string }>(items: T[]): StageGroup<T>[] {
  const groups: StageGroup<T>[] = []
  for (const item of items) {
    const named = stageOfItem(item)
    const last = groups[groups.length - 1]
    if (!last || (named && named !== last.stage)) groups.push({ stage: named ?? 'record', items: [item] })
    else last.items.push(item)
  }
  return groups
}
