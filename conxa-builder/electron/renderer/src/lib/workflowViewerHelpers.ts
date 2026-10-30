import type { StepEditorDTO, WorkflowResponse } from '@/types/workflow'
import { RECORDING_DRAG_MODE_CLEAR_VISUAL } from '@/api/workflowApi'

/** Parses a step's own `id` — `"{skillId}:{stepIndex}"` for a top-level step, or
 * `"{skillId}:{stepIndex}.for_each.steps[{j}]"` / `"...branch.steps[{j}]"` for a nested one
 * (see `conxa_compile/editor/workflow_dto.py`'s `dto_id` construction) — into its addressable
 * parts. Shared so every caller that needs to re-locate a step after a patch (nested or not)
 * parses the same shape the backend produces, instead of a second hand-rolled regex drifting. */
export function parseStepDtoId(
  id: string,
): { stepIndex: number; nested: { container: 'for_each' | 'branch'; index: number } | null } | null {
  const nestedMatch = /^.+:(\d+)\.(for_each|branch)\.steps\[(\d+)\]$/.exec(id)
  if (nestedMatch) {
    return {
      stepIndex: Number(nestedMatch[1]),
      nested: { container: nestedMatch[2] as 'for_each' | 'branch', index: Number(nestedMatch[3]) },
    }
  }
  const topMatch = /^.+:(\d+)$/.exec(id)
  if (topMatch) return { stepIndex: Number(topMatch[1]), nested: null }
  return null
}

/** Re-locates a step's fresh DTO in a just-patched workflow response by its stable `id` — works
 * for a top-level step or a nested for_each/branch body step alike. Use this instead of matching
 * on `step_index` after a patch: a nested step's own `.step_index` field is its LOCAL ordinal
 * within its container, not a position in `workflow.steps`, so matching on it directly would
 * silently resolve to the wrong (or no) step once nested paths are involved. */
export function findUpdatedStep(workflow: WorkflowResponse, id: string): StepEditorDTO | undefined {
  const parsed = parseStepDtoId(id)
  if (!parsed) return workflow.steps.find((s) => s.id === id)
  const parent = workflow.steps.find((s) => s.step_index === parsed.stepIndex)
  if (!parent) return undefined
  if (!parsed.nested) return parent
  const list = parsed.nested.container === 'for_each' ? parent.for_each_steps : parent.branch_steps
  return list[parsed.nested.index]
}

export const ADD_ACTION_OPTIONS = [
  { value: 'navigate', label: 'Navigate', category: 'Flow' },
  { value: 'scroll', label: 'Scroll', category: 'Flow' },
  { value: 'wait', label: 'Wait', category: 'Flow' },
  { value: 'wait_until', label: 'Wait until', category: 'Flow' },
  { value: 'call_workflow', label: 'Call another workflow', category: 'Flow' },
  { value: 'clipboard_write', label: 'Copy to clipboard', category: 'Advanced' },
  { value: 'clipboard_read', label: 'Read clipboard', category: 'Advanced' },
  { value: 'check', label: 'Check', category: 'Validation' },
  { value: 'assert', label: 'Assert', category: 'Validation' },
  { value: 'screenshot', label: 'Screenshot', category: 'Validation' },
  { value: 'ai_review', label: 'AI Review', category: 'Validation' },
  { value: 'click', label: 'Click', category: 'Pointer' },
  { value: 'dblclick', label: 'Double click', category: 'Pointer' },
  { value: 'right_click', label: 'Right click', category: 'Pointer' },
  { value: 'hover', label: 'Hover', category: 'Pointer' },
  { value: 'focus', label: 'Focus', category: 'Pointer' },
  { value: 'type', label: 'Type', category: 'Input' },
  { value: 'fill', label: 'Fill', category: 'Input' },
  { value: 'set_checkbox', label: 'Set checkbox', category: 'Input' },
  { value: 'set_radio', label: 'Set radio', category: 'Input' },
  { value: 'select', label: 'Select', category: 'Input' },
  { value: 'select_option', label: 'Select option', category: 'Input' },
  { value: 'date_pick', label: 'Date pick', category: 'Input' },
  { value: 'drag_drop', label: 'Drag and drop', category: 'Advanced' },
  { value: 'keyboard_shortcut', label: 'Keyboard shortcut', category: 'Advanced' },
  { value: 'upload', label: 'Upload', category: 'Advanced' },
  { value: 'if_present', label: 'If present…', category: 'Conditional' },
  { value: 'try_dismiss', label: 'Try dismiss', category: 'Conditional' },
  { value: 'wait_for_one_of', label: 'Wait for one of…', category: 'Conditional' },
] as const

export type AddActionKind = (typeof ADD_ACTION_OPTIONS)[number]['value']

export function compactStepLabel(label: string): string {
  return label.replace(/^Step\s+\d+:\s*/i, '').trim()
}

export function visualBboxState(step: StepEditorDTO): {
  usable: boolean
  label: string
  title: string
} | null {
  if (step.flags.is_scroll) return null
  const bbox = step.screenshot.bbox || {}
  const x = Number(bbox.x ?? 0)
  const y = Number(bbox.y ?? 0)
  const w = Number(bbox.w ?? 0)
  const h = Number(bbox.h ?? 0)
  if (w >= 2 && h >= 2) {
    return {
      usable: true,
      label: `${Math.round(w)}x${Math.round(h)}`,
      title: `Visual bbox saved: x ${Math.round(x)}, y ${Math.round(y)}, w ${Math.round(w)}, h ${Math.round(h)}`,
    }
  }
  return {
    usable: false,
    label: 'missing',
    title: 'No usable visual bbox saved for this action.',
  }
}

export type BboxState = NonNullable<ReturnType<typeof visualBboxState>>

export function handleRecordingScreenshotDrop(
  raw: string,
  stepIndex: number,
  onDroppedRecordingScreenshot?: (stepIndex: number, eventIndex: number) => void,
  onClearStepVisual?: (stepIndex: number) => void,
) {
  try {
    const parsed = JSON.parse(raw) as { event_index?: unknown; mode?: unknown }
    if (parsed.mode === RECORDING_DRAG_MODE_CLEAR_VISUAL && onClearStepVisual) {
      void onClearStepVisual(stepIndex)
      return
    }
    const eventIndex = parsed.event_index
    if (typeof eventIndex === 'number' && Number.isFinite(eventIndex) && eventIndex >= 0) {
      void onDroppedRecordingScreenshot?.(stepIndex, Math.floor(eventIndex))
    }
  } catch {
    // Ignore malformed drag payloads from unrelated sources.
  }
}
