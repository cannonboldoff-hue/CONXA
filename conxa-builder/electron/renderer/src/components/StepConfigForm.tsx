import { forwardRef, memo, useCallback, useEffect, useImperativeHandle, useState } from 'react'
import { FormProvider, useForm, useFormState, useWatch } from 'react-hook-form'
import { toast } from 'sonner'
import type { StepEditorDTO, WorkflowResponse } from '../types/workflow'
import { patchStep, errorMessage } from '../api/workflowApi'
import { useEditorStore } from '../store/editorStore'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { InfoHint } from '@/components/ui/info-hint'
import { editorHelp } from '@/lib/editorHelp'
import { fieldSelectClass, fieldTextareaClass } from '@/lib/fieldStyles'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { ChevronRight, Trash2 } from 'lucide-react'
import { AssertionEditorRows, describeWaitFor, hasInvalidAssertions, type AssertionDraft } from '@/components/validation/AssertionEditor'
import { ElementFingerprintCard } from '@/components/ElementFingerprintCard'
import { anchorRowsFromObjects, parseAnchorRows } from '@/lib/anchors'
import { findUpdatedStep } from '@/lib/workflowViewerHelpers'

// Same gradient-fill + ring depth treatment as PanelChrome (components/ui/panel-chrome.tsx),
// layered onto Card's className rather than swapping the component itself — Card's
// data-size-scaled CardHeader/CardContent padding relies on the group/card + data-[size] wiring
// that a plain PanelChrome div wrapper doesn't provide.
const PANEL_CARD_CLASS =
  'bg-[linear-gradient(180deg,rgba(17,24,39,0.85),rgba(7,10,16,0.92))] ring-white/10'

type FormValues = {
  intent: string
  url: string
  scroll_mode: 'scroll_only' | 'scroll_to_locate'
  scroll_amount: string
  scroll_selector: string
  selectors: string[]
  value: string
  css: string
  aria: string
  text_based: string
  xpath: string
  anchors: string[]
  check_kind: string
  check_pattern: string
  check_threshold: string
  check_selector: string
  check_text: string
  ai_review_prompt: string
  ai_review_output_schema_preset: 'yes_no' | 'text' | 'none' | 'custom'
  ai_review_output_schema_json: string
  ai_review_on_failure: 'abort' | 'use_default' | 'continue'
  ai_review_default_value: string
  assertions: AssertionDraft[]
  // BUILD-51: when a step runs, what to do when its record is gone, and what an extract step reads
  run_if_input: string
  run_if_op: 'not_empty' | 'equals' | 'not_equals'
  run_if_value: string
  on_missing: '' | 'skip' | 'ask' | 'fail'
  extract_source: 'text' | 'value' | 'attribute'
  extract_attribute: string
  extract_pattern: string
}

// EXEC-13: canonical shapes the preset picker offers — 'custom' isn't a real option, it's what
// defaultsFromStep falls back to when a saved schema doesn't match any preset, so the raw JSON
// textarea (always editable underneath) is the source of truth rather than the picker.
const AI_REVIEW_SCHEMA_PRESETS: Record<'yes_no' | 'text' | 'none', Record<string, unknown> | null> = {
  yes_no: {
    type: 'object',
    required: ['answer', 'why'],
    properties: { answer: { type: 'string', enum: ['yes', 'no'] }, why: { type: 'string' } },
  },
  text: {
    type: 'object',
    required: ['value'],
    properties: { value: { type: 'string' } },
  },
  none: null,
}

function presetForAiReviewSchema(schema: unknown): FormValues['ai_review_output_schema_preset'] {
  if (!schema || typeof schema !== 'object') return 'none'
  const json = JSON.stringify(schema)
  for (const key of ['yes_no', 'text'] as const) {
    if (JSON.stringify(AI_REVIEW_SCHEMA_PRESETS[key]) === json) return key
  }
  return 'custom'
}

const emptyForm: FormValues = {
  intent: '',
  url: '',
  scroll_mode: 'scroll_only',
  scroll_amount: '',
  scroll_selector: '',
  selectors: [''],
  value: '',
  css: '',
  aria: '',
  text_based: '',
  xpath: '',
  anchors: [''],
  check_kind: 'url',
  check_pattern: '',
  check_threshold: '0.9',
  check_selector: '',
  check_text: '',
  ai_review_prompt: '',
  ai_review_output_schema_preset: 'yes_no',
  ai_review_output_schema_json: JSON.stringify(AI_REVIEW_SCHEMA_PRESETS.yes_no, null, 2),
  ai_review_on_failure: 'abort',
  ai_review_default_value: '',
  assertions: [],
  run_if_input: '',
  run_if_op: 'not_empty',
  run_if_value: '',
  on_missing: '',
  extract_source: 'text',
  extract_attribute: '',
  extract_pattern: '',
}

const URL_CHECK_KINDS = new Set(['url', 'url_exact', 'url_must_be'])
const EXACT_URL_CHECK_KINDS = new Set(['url_exact', 'url_must_be'])

function frameChainFromStep(step: StepEditorDTO): Record<string, unknown>[] {
  const frame = step.frame || {}
  const chain = Array.isArray(frame.chain) ? frame.chain : []
  return chain.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
}

function defaultsFromStep(step: StepEditorDTO): FormValues {
  const tgt = step.target as { primary_selector?: string; fallback_selectors?: string[] }
  const sel = step.selectors as { css?: string; aria?: string; text_based?: string; xpath?: string }
  const compiledSelectors = Array.isArray(step.compiled_selectors)
    ? step.compiled_selectors.map((selector) => String(selector || '').trim()).filter(Boolean)
    : []
  const targetSelectors = [String(tgt.primary_selector || ''), ...(tgt.fallback_selectors || [])].filter(
    (selector, index, arr) => index === 0 || Boolean(selector) || arr.length === 1,
  )
  const actionPayload = step.action_payload || {}
  const anc = anchorRowsFromObjects(step.anchors_signals || [])
  return {
    // Show the workflow-intent graph's readable sentence when present (same source as the
    // Workflow plan panel); fall back to the machine token for steps compiled before
    // prose existed. Edits go back as `semantic_description` patches, never as `intent`.
    intent: step.semantic_description?.trim() || step.intent || step.final_intent || '',
    url: step.url || '',
    scroll_mode: step.scroll_mode === 'scroll_to_locate' ? 'scroll_to_locate' : 'scroll_only',
    scroll_amount: step.scroll_amount === null || step.scroll_amount === undefined ? '' : String(step.scroll_amount),
    scroll_selector: String(step.scroll_selector || ''),
    selectors: targetSelectors.some(Boolean) ? targetSelectors : (compiledSelectors.length > 0 ? compiledSelectors : ['']),
    value:
      typeof step.value === 'string'
        ? step.value
        : typeof actionPayload.value === 'string'
          ? actionPayload.value
          : actionPayload.ms !== undefined && actionPayload.ms !== null
            ? String(actionPayload.ms)
            : step.value !== undefined && step.value !== null
              ? String(step.value)
              : '',
    css: String(sel.css || ''),
    aria: String(sel.aria || ''),
    text_based: String(sel.text_based || ''),
    xpath: String(sel.xpath || ''),
    anchors: anc.length > 0 ? anc : [''],
    check_kind: String(step.check_kind || 'url'),
    check_pattern: String(step.check_pattern || ''),
    check_threshold: String(step.check_threshold ?? 0.9),
    check_selector: String(step.check_selector || ''),
    check_text: String(step.check_text || ''),
    ai_review_prompt: String(step.ai_review_prompt || ''),
    ai_review_output_schema_preset: presetForAiReviewSchema(step.ai_review_output_schema),
    ai_review_output_schema_json: step.ai_review_output_schema
      ? JSON.stringify(step.ai_review_output_schema, null, 2)
      : '',
    ai_review_on_failure: (step.ai_review_on_failure as FormValues['ai_review_on_failure']) || 'abort',
    ai_review_default_value:
      step.ai_review_default_value === undefined || step.ai_review_default_value === null
        ? ''
        : typeof step.ai_review_default_value === 'string'
          ? step.ai_review_default_value
          : JSON.stringify(step.ai_review_default_value),
    assertions: (step.validation.assertions || []) as AssertionDraft[],
    run_if_input: String(step.run_if?.input || ''),
    run_if_op: (step.run_if?.op as FormValues['run_if_op']) || 'not_empty',
    run_if_value: String(step.run_if?.value || ''),
    on_missing: (step.on_missing as FormValues['on_missing']) || '',
    extract_source: (step.extract?.source as FormValues['extract_source']) || 'text',
    extract_attribute: String(step.extract?.attribute || ''),
    extract_pattern: String(step.extract?.pattern || ''),
  }
}

/** The part of a patch every element step shares: its run condition and its "record is gone" choice.
 *  Sent only when there is something to set or something to clear, so a step that never had one is not
 *  touched. The patch gate refuses a condition on a confirmation or hand-over, so those never get here. */
function conditionPatch(step: StepEditorDTO, values: FormValues): Record<string, unknown> {
  const patch: Record<string, unknown> = {}
  const input = values.run_if_input.trim()
  if (input || step.run_if?.input) {
    patch.run_if = input
      ? {
          input,
          op: values.run_if_op,
          ...(values.run_if_op === 'equals' || values.run_if_op === 'not_equals' ? { value: values.run_if_value } : {}),
        }
      : {}
  }
  if (Object.keys(step.entity_binding || {}).length) patch.on_missing = values.on_missing
  return patch
}

type Props = {
  step: StepEditorDTO | null
  skillId: string
  onWorkflowUpdated: (wf: WorkflowResponse) => void
  onHistoryUpdate?: (canUndo: boolean, canRedo: boolean) => void
  /** Hide the free-text selector list / CSS-ARIA-XPath channel cards / anchors, and the
   *  Validation panel — used when the re-target flow's own phases own selector picking and
   *  validation editing instead. */
  hideSelectorTools?: boolean
  /** Hide the "Save step" submit button — used when an outer flow already calls
   *  `submitIfDirty()` itself (e.g. the re-target wizard's Continue button), so a second,
   *  redundant manual save action isn't shown alongside it. */
  hideSubmitButton?: boolean
  /** Set when `step` is a nested for_each loop-body step instead of a top-level one — addresses
   *  the patch as `patchStep(skillId, parentStepIndex, patch, false, path)` (see
   *  `cmd_patch_step`'s `path` parameter) rather than by `step.step_index`, which for a nested
   *  step is only its LOCAL ordinal within the loop, not a position in the top-level step list.
   *  Omit for an ordinary top-level step (unchanged behavior). */
  path?: string
  /** The top-level step's real index — required alongside `path`, ignored otherwise (defaults
   *  to `step.step_index`, correct for a top-level step). */
  parentStepIndex?: number
}

export type StepConfigFormHandle = {
  /** Saves the open step form if dirty. Returns whether save succeeded or was not needed. */
  submitIfDirty: () => Promise<boolean>
}

function stepActionKind(step: StepEditorDTO): string {
  return step.action_type.trim().toLowerCase().replace(/-/g, '_')
}

function actionSpecFlag(step: StepEditorDTO, key: string): boolean {
  return Boolean((step.action_spec || {})[key])
}

function actionValueLabel(step: StepEditorDTO): string {
  const raw = (step.action_spec || {}).value_label
  return typeof raw === 'string' && raw.trim() ? raw : 'Value'
}

function parseScrollAmount(raw: string): number {
  const trimmed = raw.trim()
  if (!trimmed) throw new Error('Scroll amount is required')
  if (!/^-?\d+$/.test(trimmed)) throw new Error('Scroll amount must be a whole number')
  return Number.parseInt(trimmed, 10)
}

function DirtySync({ stepIndex }: { stepIndex: number }) {
  const { isDirty } = useFormState()
  const markDirty = useEditorStore((s) => s.markStepDirty)
  const clearDirty = useEditorStore((s) => s.clearStepDirty)
  useEffect(() => {
    if (isDirty) markDirty(stepIndex)
    else clearDirty(stepIndex)
  }, [isDirty, stepIndex, markDirty, clearDirty])
  return null
}

type StepValidationPanelProps = {
  step: StepEditorDTO
  assertions: AssertionDraft[]
  onChange: (next: AssertionDraft[]) => void
}

/** Shows what confirms this step actually worked, and lets a human edit that check by hand
 *  (`docs/TRD.md` §9.5 VERIFY). Edits live in the same form state as the rest of the step —
 *  one "Save step" action saves both, instead of a second independent save control. */
function StepValidationPanel({ step, assertions, onChange }: StepValidationPanelProps) {
  return (
    <Card className={cn('gap-2 py-3', PANEL_CARD_CLASS)}>
      <CardHeader className="p-2.5 pb-1">
        <CardTitle className="flex items-center gap-2 text-base font-semibold">
          Validation
          <InfoHint {...editorHelp.toolValidation} size="md" side="bottom" align="start" />
        </CardTitle>
        <CardDescription className="text-xs">{describeWaitFor(step.validation.wait_for || {})}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 p-2.5 pt-0">
        <AssertionEditorRows assertions={assertions} onChange={onChange} />
      </CardContent>
    </Card>
  )
}

// memo: this stays mounted across all three re-target wizard phases (see InlineRetargetFlow's
// "kept mounted" comment) and that parent re-renders on every candidate edit/reorder in the
// Review Selectors phase — memo stops this form (react-hook-form + several child cards) from
// re-rendering on candidate changes that don't touch any of its own props.
export const StepConfigForm = memo(forwardRef<StepConfigFormHandle, Props>(
  function StepConfigForm(
    { step, skillId, onWorkflowUpdated, onHistoryUpdate, hideSelectorTools, hideSubmitButton, path, parentStepIndex },
    ref,
  ) {
  // The index/path pair every patchStep call below addresses — step.step_index directly for an
  // ordinary top-level step, or the caller-supplied parent + path for a nested for_each step
  // (see this file's Props.path doc comment).
  const patchIndex = parentStepIndex ?? step?.step_index ?? 0
  const methods = useForm<FormValues>({ defaultValues: step ? defaultsFromStep(step) : emptyForm })
  const [advancedOpen, setAdvancedOpen] = useState(false)

  // Re-sync from the server whenever `step` gets a new reference — but only when the form has
  // no unsaved edits. `step` (HumanEditPage's currentStep) is re-derived from the workflow query
  // cache and gets a fresh reference any time that cache is replaced for ANY reason, including
  // ones unrelated to this step: a background refetch (window focus after staleTime), or a
  // sibling save on the same page (e.g. RecoveryAnchorsCard, rendered alongside this form in the
  // re-target wizard's Review Selectors phase). Resetting unconditionally on every reference
  // change silently discarded whatever the user had just typed and cleared isDirty, so
  // submitIfDirty()'s Continue-button save saw "nothing to save" and skipped the write entirely
  // — no error, edit just vanished. Genuine step switches remount this component (see
  // InlineRetargetFlow's key={currentStep?.id}), giving a fresh isDirty=false form regardless of
  // this guard, so this only ever protects an in-progress edit on the step already open.
  useEffect(() => {
    if (!step) return
    if (methods.formState.isDirty) return
    methods.reset(defaultsFromStep(step))
  }, [step, methods])

  const persistStepValues = useCallback(
    async (values: FormValues, options?: { silentToast?: boolean }) => {
      if (!step) return
      const silent = options?.silentToast ?? false
      const editable = step.editable_fields
      const canEditField = (key: string) => editable[key] !== false
      const actionKind = stepActionKind(step)
      const isMarkerStep = actionSpecFlag(step, 'marker')
      const isCheckStep = actionKind === 'check' || actionKind === 'assert'
      const isNavigateStep = actionKind === 'navigate'
      const isAiReviewStep = actionKind === 'ai_review'
      if (isMarkerStep) return
      if (isAiReviewStep) {
        const prompt = values.ai_review_prompt.trim()
        if (!prompt) {
          const err = new Error('Prompt is required')
          methods.setError('ai_review_prompt', { message: err.message })
          if (!silent) toast.error(err.message)
          throw err
        }
        let outputSchema: Record<string, unknown> | null = null
        const schemaText = values.ai_review_output_schema_json.trim()
        if (schemaText) {
          try {
            const parsed = JSON.parse(schemaText) as unknown
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('not an object')
            outputSchema = parsed as Record<string, unknown>
          } catch {
            const err = new Error('Expected answer schema must be valid JSON (an object)')
            methods.setError('ai_review_output_schema_json', { message: err.message })
            if (!silent) toast.error(err.message)
            throw err
          }
        }
        const onFailure = values.ai_review_on_failure
        if (onFailure === 'use_default' && !values.ai_review_default_value.trim()) {
          const err = new Error('A default value is required when "Use a default value" is selected')
          methods.setError('ai_review_default_value', { message: err.message })
          if (!silent) toast.error(err.message)
          throw err
        }
        const patch: Record<string, unknown> = {
          semantic_description: values.intent,
          action: { action: 'ai_review' },
          ai_review_prompt: prompt,
          ai_review_output_schema: outputSchema,
          ai_review_on_failure: onFailure,
        }
        if (onFailure === 'use_default') {
          // A default value is free-form: an author typing `{"answer":"no"}` gets a real object
          // bound back into inputs on failure; plain text (e.g. `no`) is kept as a string.
          let defaultValue: unknown = values.ai_review_default_value
          try { defaultValue = JSON.parse(values.ai_review_default_value) } catch { /* keep raw string */ }
          patch.ai_review_default_value = defaultValue
        }
        if (canEditField('value')) {
          patch.value = values.value
          patch.action = { action: 'ai_review', value: values.value }
        }
        try {
          const res = await patchStep(skillId, patchIndex, patch, false, path)
          onWorkflowUpdated(res.workflow)
          if (res.can_undo !== undefined) onHistoryUpdate?.(res.can_undo, res.can_redo ?? false)
          const next = findUpdatedStep(res.workflow, step.id)
          if (next) methods.reset(defaultsFromStep(next))
          if (!silent) toast.success('Step saved')
          return
        } catch (e) {
          const msg = errorMessage(e, 'Save failed')
          methods.setError('root', { message: msg })
          if (!silent) toast.error(msg)
          throw e
        }
      }
      if (isNavigateStep) {
        const url = values.url.trim()
        if (!/^https?:\/\//i.test(url)) {
          const err = new Error('Navigate URL must start with http:// or https://')
          methods.setError('url', { message: err.message })
          if (!silent) toast.error(err.message)
          throw err
        }
        const patch: Record<string, unknown> = {
          semantic_description: values.intent,
          url,
          action: {
            action: 'navigate',
            url,
          },
          validation: {
            wait_for: { type: 'url_change', target: url, timeout: 60000 },
            success_conditions: { url },
          },
        }
        if (canEditField('validation')) {
          patch.validation = { ...(patch.validation as Record<string, unknown>), assertions: values.assertions }
        }
        try {
          const res = await patchStep(skillId, patchIndex, patch, false, path)
          onWorkflowUpdated(res.workflow)
          if (res.can_undo !== undefined) onHistoryUpdate?.(res.can_undo, res.can_redo ?? false)
          const next = findUpdatedStep(res.workflow, step.id)
          if (next) methods.reset(defaultsFromStep(next))
          if (!silent) toast.success('Step saved')
          return
        } catch (e) {
          const msg = errorMessage(e, 'Save failed')
          methods.setError('root', { message: msg })
          if (!silent) toast.error(msg)
          throw e
        }
      }
      if (actionKind === 'wait') {
        const rawMs = values.value.trim() || '1000'
        if (!/^\d+$/.test(rawMs)) {
          const err = new Error('Wait milliseconds must be a non-negative whole number')
          methods.setError('value', { message: err.message })
          if (!silent) toast.error(err.message)
          throw err
        }
        const ms = Number.parseInt(rawMs, 10)
        const patch: Record<string, unknown> = {
          semantic_description: values.intent,
          value: String(ms),
          action: {
            action: 'wait',
            ms,
            value: String(ms),
          },
        }
        if (canEditField('validation')) patch.validation = { assertions: values.assertions }
        try {
          const res = await patchStep(skillId, patchIndex, patch, false, path)
          onWorkflowUpdated(res.workflow)
          if (res.can_undo !== undefined) onHistoryUpdate?.(res.can_undo, res.can_redo ?? false)
          const next = findUpdatedStep(res.workflow, step.id)
          if (next) methods.reset(defaultsFromStep(next))
          if (!silent) toast.success('Step saved')
          return
        } catch (e) {
          const msg = errorMessage(e, 'Save failed')
          methods.setError('root', { message: msg })
          if (!silent) toast.error(msg)
          throw e
        }
      }
      if (actionKind === 'screenshot') {
        const patch: Record<string, unknown> = {
          semantic_description: values.intent,
          action: {
            action: 'screenshot',
          },
        }
        if (canEditField('validation')) patch.validation = { assertions: values.assertions }
        try {
          const res = await patchStep(skillId, patchIndex, patch, false, path)
          onWorkflowUpdated(res.workflow)
          if (res.can_undo !== undefined) onHistoryUpdate?.(res.can_undo, res.can_redo ?? false)
          const next = findUpdatedStep(res.workflow, step.id)
          if (next) methods.reset(defaultsFromStep(next))
          if (!silent) toast.success('Step saved')
          return
        } catch (e) {
          const msg = errorMessage(e, 'Save failed')
          methods.setError('root', { message: msg })
          if (!silent) toast.error(msg)
          throw e
        }
      }
      if (isCheckStep) {
        const patch: Record<string, unknown> = {
          semantic_description: values.intent,
          action: {
            action: actionKind,
          },
          check_kind: values.check_kind,
        }
        if (URL_CHECK_KINDS.has(values.check_kind)) {
          patch.check_pattern = values.check_pattern
          patch.signals = { anchors: [] }
          patch.recovery = { anchors: [] }
        }
        else if (values.check_kind === 'snapshot') patch.check_threshold = Number(values.check_threshold)
        else if (values.check_kind === 'selector') patch.check_selector = values.check_selector
        else if (values.check_kind === 'text') patch.check_text = values.check_text
        if (canEditField('validation')) patch.validation = { assertions: values.assertions }
        try {
          const res = await patchStep(skillId, patchIndex, patch, false, path)
          onWorkflowUpdated(res.workflow)
          if (res.can_undo !== undefined) onHistoryUpdate?.(res.can_undo, res.can_redo ?? false)
          const next = findUpdatedStep(res.workflow, step.id)
          if (next) methods.reset(defaultsFromStep(next))
          if (!silent) toast.success('Step saved')
          return
        } catch (e) {
          const msg = errorMessage(e, 'Save failed')
          methods.setError('root', { message: msg })
          if (!silent) toast.error(msg)
          throw e
        }
      }
      if (actionKind === 'extract') {
        const name = values.value.trim()
        if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(name)) {
          const err = new Error('The result name must start with a letter and use only letters, numbers and underscores')
          methods.setError('value', { message: err.message })
          if (!silent) toast.error(err.message)
          throw err
        }
        const patch: Record<string, unknown> = {
          semantic_description: values.intent,
          value: name,
          extract: {
            source: values.extract_source,
            ...(values.extract_source === 'attribute' ? { attribute: values.extract_attribute.trim() } : {}),
            ...(values.extract_pattern.trim() ? { pattern: values.extract_pattern.trim() } : {}),
          },
          ...conditionPatch(step, values),
        }
        if (canEditField('validation')) patch.validation = { assertions: values.assertions }
        try {
          const res = await patchStep(skillId, patchIndex, patch, false, path)
          onWorkflowUpdated(res.workflow)
          if (res.can_undo !== undefined) onHistoryUpdate?.(res.can_undo, res.can_redo ?? false)
          const next = findUpdatedStep(res.workflow, step.id)
          if (next) methods.reset(defaultsFromStep(next))
          if (!silent) toast.success('Step saved')
          return
        } catch (e) {
          const msg = errorMessage(e, 'Save failed')
          methods.setError('root', { message: msg })
          if (!silent) toast.error(msg)
          throw e
        }
      }
      if (step.flags.is_scroll) {
        const patch: Record<string, unknown> = {
          semantic_description: values.intent,
          action: {
            action: 'scroll',
          },
        }
        if (values.scroll_mode === 'scroll_to_locate') {
          const selector = values.scroll_selector.trim()
          if (!selector) {
            const err = new Error('Scroll target selector is required')
            methods.setError('scroll_selector', { message: err.message })
            if (!silent) toast.error(err.message)
            throw err
          }
          patch.action = {
            action: 'scroll',
            selector,
          }
        } else {
          const scrollAmount = parseScrollAmount(values.scroll_amount)
          patch.action = {
            action: 'scroll',
            delta: scrollAmount,
          }
        }
        if (canEditField('validation')) patch.validation = { assertions: values.assertions }
        try {
          const res = await patchStep(skillId, patchIndex, patch, false, path)
          onWorkflowUpdated(res.workflow)
          if (res.can_undo !== undefined) onHistoryUpdate?.(res.can_undo, res.can_redo ?? false)
          const next = findUpdatedStep(res.workflow, step.id)
          if (next) methods.reset(defaultsFromStep(next))
          if (!silent) toast.success('Step saved')
          return
        } catch (e) {
          const msg = errorMessage(e, 'Save failed')
          methods.setError('root', { message: msg })
          if (!silent) toast.error(msg)
          throw e
        }
      }
      const selectors = values.selectors
        .map((s) => s.trim())
        .filter(Boolean)
      const primarySelector = selectors[0] || ''
      const fallbackSelectors = selectors.slice(1)
      const anchors = parseAnchorRows(values.anchors)
      const patch: Record<string, unknown> = {
        semantic_description: values.intent,
        action: {
          action: actionKind,
        },
        target: {
          primary_selector: primarySelector,
          fallback_selectors: fallbackSelectors,
        },
        signals: {
          selectors: {
            css: values.css || primarySelector,
            aria: values.aria,
            text_based: values.text_based,
            xpath: values.xpath,
          },
          anchors,
        },
      }
      if (canEditField('value')) {
        patch.value = values.value
        patch.action = {
          action: actionKind,
          value: values.value,
        }
      }
      if (canEditField('validation')) patch.validation = { assertions: values.assertions }
      Object.assign(patch, conditionPatch(step, values))
      try {
        const res = await patchStep(skillId, patchIndex, patch, false, path)
        onWorkflowUpdated(res.workflow)
        if (res.can_undo !== undefined) onHistoryUpdate?.(res.can_undo, res.can_redo ?? false)
        const next = findUpdatedStep(res.workflow, step.id)
        if (next) methods.reset(defaultsFromStep(next))
        if (!silent) toast.success('Step saved')
      } catch (e) {
        const msg = errorMessage(e, 'Save failed')
        methods.setError('root', { message: msg })
        if (!silent) toast.error(msg)
        throw e
      }
    },
    [methods, onHistoryUpdate, onWorkflowUpdated, skillId, step, patchIndex, path],
  )

  useImperativeHandle(
    ref,
    () => ({
      submitIfDirty: async () => {
        if (!step) return true
        if (!methods.formState.isDirty) return true
        return await new Promise<boolean>((resolve) => {
          void methods.handleSubmit(
            async (values) => {
              try {
                await persistStepValues(values, { silentToast: true })
                resolve(true)
              } catch {
                resolve(false)
              }
            },
            () => resolve(false),
          )()
        })
      },
    }),
    [methods, persistStepValues, step],
  )

  const selectors = useWatch({ control: methods.control, name: 'selectors' }) || ['']
  const anchors = useWatch({ control: methods.control, name: 'anchors' }) || ['']
  const checkKind = useWatch({ control: methods.control, name: 'check_kind' }) || 'url'
  const scrollMode = useWatch({ control: methods.control, name: 'scroll_mode' }) || 'scroll_only'
  const assertions = useWatch({ control: methods.control, name: 'assertions' }) || []
  const aiReviewSchemaPreset = useWatch({ control: methods.control, name: 'ai_review_output_schema_preset' }) || 'yes_no'
  const aiReviewOnFailure = useWatch({ control: methods.control, name: 'ai_review_on_failure' }) || 'abort'
  const extractSource = useWatch({ control: methods.control, name: 'extract_source' }) || 'text'
  const runIfOp = useWatch({ control: methods.control, name: 'run_if_op' }) || 'not_empty'

  if (!step) {
    return (
      <div className="text-muted-foreground flex min-h-0 min-w-0 items-center justify-center p-4 text-sm">
        Select a step to edit
      </div>
    )
  }

  const editable = step.editable_fields
  const canEdit = (key: string) => editable[key] !== false
  const actionKind = stepActionKind(step)
  const isMarkerStep = actionSpecFlag(step, 'marker')
  const actionHasSelectors = actionSpecFlag(step, 'selectors')
  const actionHasValue = actionSpecFlag(step, 'value')
  const isCheckStep = actionKind === 'check' || actionKind === 'assert'
  const isNavigateStep = actionKind === 'navigate'
  const isScrollStep = step.flags.is_scroll || actionKind === 'scroll'
  const isWaitStep = actionKind === 'wait'
  const isScreenshotStep = actionKind === 'screenshot'
  const isAiReviewStep = actionKind === 'ai_review'
  const isExtractStep = actionKind === 'extract'
  const isBoundStep = Object.keys(step.entity_binding || {}).length > 0
  // The patch gate refuses a run condition on a confirmation, hand-over or marker, and a popup decision.
  const canHaveCondition = !isMarkerStep && !isAiReviewStep && actionKind !== 'handover' && actionKind !== 'outcome_fork'
    && actionKind !== 'for_each' && !isCheckStep
  const showSelectorAndAnchorTools =
    !hideSelectorTools &&
    actionHasSelectors && !isScrollStep && !isNavigateStep && !isMarkerStep && !(isCheckStep && URL_CHECK_KINDS.has(checkKind))
  const frameChain = frameChainFromStep(step)
  const assertionsInvalid = !hideSelectorTools && canEdit('validation') && hasInvalidAssertions(assertions)

  const onSubmit = methods.handleSubmit(async (values) => {
    try {
      await persistStepValues(values, { silentToast: false })
    } catch {
      /* persistStepValues surfaces toast/error state */
    }
  })

  return (
    <FormProvider {...methods}>
      <DirtySync stepIndex={patchIndex} />
      <form onSubmit={onSubmit} className="space-y-7">
          <div className="grid gap-2">
            <Label htmlFor="intent" className="flex items-center gap-1.5 text-sm font-medium">
              Description
              <InfoHint {...editorHelp.actionStep} size="md" side="bottom" align="start" />
            </Label>
            <Input
              id="intent"
              type="text"
              disabled={!canEdit('intent')}
              className="h-[46px] rounded-[10px] text-[15px]"
              {...methods.register('intent')}
            />
            {step.intent && step.intent.trim() !== methods.getValues('intent').trim() ? (
              <p className="text-muted-foreground font-mono text-xs">
                Machine intent: {step.intent}
              </p>
            ) : null}
          </div>
          {isCheckStep ? (
            <>
              <div className="grid gap-2">
                <Label htmlFor="check_kind">Check type</Label>
                <select
                  id="check_kind"
                  className={fieldSelectClass}
                  {...methods.register('check_kind')}
                >
                  <option value="url">URL contains pattern</option>
                  <option value="url_exact">URL must be (exact)</option>
                  <option value="url_must_be">URL must be (exact match)</option>
                  <option value="snapshot">Snapshot similarity (≥ threshold)</option>
                  <option value="selector">Element present</option>
                  <option value="text">Text appears on page</option>
                </select>
              </div>
              {URL_CHECK_KINDS.has(checkKind) && (
                <div className="grid gap-2">
                  <Label htmlFor="check_pattern">
                    {EXACT_URL_CHECK_KINDS.has(checkKind) ? 'Expected URL' : 'URL pattern (substring)'}
                  </Label>
                  <Input
                    id="check_pattern"
                    type="text"
                    placeholder={EXACT_URL_CHECK_KINDS.has(checkKind) ? 'https://example.com/dashboard' : 'e.g., /dashboard'}
                    {...methods.register('check_pattern')}
                  />
                </div>
              )}
              {checkKind === 'snapshot' && (
                <div className="grid gap-2">
                  <Label htmlFor="check_threshold" className="flex items-center gap-1.5">
                    Similarity threshold (0.0 - 1.0)
                    <InfoHint {...editorHelp.matchThreshold} side="top" align="start" />
                  </Label>
                  <Input
                    id="check_threshold"
                    type="number"
                    step="0.05"
                    min="0"
                    max="1"
                    defaultValue="0.9"
                    {...methods.register('check_threshold')}
                  />
                  <p className="text-muted-foreground text-xs">Default: 0.9 (90% match)</p>
                </div>
              )}
              {checkKind === 'selector' && (
                <div className="grid gap-2">
                  <Label htmlFor="check_selector">CSS selector</Label>
                  <Input
                    id="check_selector"
                    type="text"
                    placeholder="e.g., .success-message"
                    {...methods.register('check_selector')}
                  />
                </div>
              )}
              {checkKind === 'text' && (
                <div className="grid gap-2">
                  <Label htmlFor="check_text">Expected text</Label>
                  <Input
                    id="check_text"
                    type="text"
                    placeholder="e.g., Success"
                    {...methods.register('check_text')}
                  />
                </div>
              )}
            </>
          ) : null}
          {isAiReviewStep ? (
            <>
              <div className="grid gap-2">
                <Label htmlFor="ai_review_prompt" className="flex items-center gap-1.5">
                  Question
                  <InfoHint {...editorHelp.actionStep} size="md" side="top" align="start" />
                </Label>
                <Textarea
                  id="ai_review_prompt"
                  className={cn(fieldTextareaClass, 'min-h-20 w-full text-sm')}
                  placeholder="e.g., Is there an error banner on this page? Answer yes or no and say why."
                  {...methods.register('ai_review_prompt')}
                />
                {methods.formState.errors.ai_review_prompt ? (
                  <p className="text-destructive text-xs">{methods.formState.errors.ai_review_prompt.message}</p>
                ) : null}
              </div>
              <div className="grid gap-2">
                <Label htmlFor="ai_review_output_schema_preset">Expected answer</Label>
                <select
                  id="ai_review_output_schema_preset"
                  className={fieldSelectClass}
                  {...methods.register('ai_review_output_schema_preset', {
                    onChange: (e) => {
                      const key = e.target.value as 'yes_no' | 'text' | 'none' | 'custom'
                      if (key === 'yes_no' || key === 'text') {
                        methods.setValue(
                          'ai_review_output_schema_json',
                          JSON.stringify(AI_REVIEW_SCHEMA_PRESETS[key], null, 2),
                          { shouldDirty: true },
                        )
                      } else if (key === 'none') {
                        methods.setValue('ai_review_output_schema_json', '', { shouldDirty: true })
                      }
                    },
                  })}
                >
                  <option value="yes_no">Yes / no, with a reason</option>
                  <option value="text">A single text value</option>
                  <option value="none">No schema — accept anything</option>
                  <option value="custom" disabled hidden>
                    Custom (edit the JSON below)
                  </option>
                </select>
                <p className="text-muted-foreground text-xs">
                  Picking a preset fills in the JSON schema below — edit it directly for anything else.
                </p>
                {aiReviewSchemaPreset !== 'none' ? (
                  <Textarea
                    id="ai_review_output_schema_json"
                    className={cn(fieldTextareaClass, 'min-h-32 w-full font-mono text-xs')}
                    {...methods.register('ai_review_output_schema_json')}
                  />
                ) : null}
                {methods.formState.errors.ai_review_output_schema_json ? (
                  <p className="text-destructive text-xs">{methods.formState.errors.ai_review_output_schema_json.message}</p>
                ) : null}
              </div>
              <div className="grid gap-2">
                <Label htmlFor="ai_review_on_failure">If a usable answer never arrives</Label>
                <select id="ai_review_on_failure" className={fieldSelectClass} {...methods.register('ai_review_on_failure')}>
                  <option value="abort">Abort the run</option>
                  <option value="use_default">Use a default value</option>
                  <option value="continue">Continue anyway</option>
                </select>
              </div>
              {aiReviewOnFailure === 'use_default' ? (
                <div className="grid gap-2">
                  <Label htmlFor="ai_review_default_value">Default value</Label>
                  <Input
                    id="ai_review_default_value"
                    type="text"
                    placeholder='e.g., {"answer":"no","why":"no answer received"} or a plain value'
                    {...methods.register('ai_review_default_value')}
                  />
                  {methods.formState.errors.ai_review_default_value ? (
                    <p className="text-destructive text-xs">{methods.formState.errors.ai_review_default_value.message}</p>
                  ) : null}
                </div>
              ) : null}
            </>
          ) : null}
          {isNavigateStep ? (
            <div className="grid gap-2">
              <Label htmlFor="url" className="text-sm font-medium">Page address</Label>
              <Input
                id="url"
                type="url"
                placeholder="https://example.com"
                disabled={!canEdit('url')}
                className="h-[46px] rounded-[10px] text-[15px]"
                {...methods.register('url')}
              />
              {methods.formState.errors.url ? (
                <p className="text-destructive text-xs">{methods.formState.errors.url.message}</p>
              ) : null}
            </div>
          ) : null}
          {isScrollStep ? (
            <>
              <div className="grid gap-2">
                <Label htmlFor="scroll_mode">Scroll mode</Label>
                <select id="scroll_mode" className={fieldSelectClass} {...methods.register('scroll_mode')}>
                  <option value="scroll_only">Scroll only</option>
                  <option value="scroll_to_locate">Scroll to locate</option>
                </select>
              </div>
              {scrollMode === 'scroll_to_locate' ? (
                <div className="grid gap-2">
                  <Label htmlFor="scroll_selector">Target selector</Label>
                  <Input
                    id="scroll_selector"
                    type="text"
                    placeholder="text=Load more"
                    {...methods.register('scroll_selector')}
                  />
                </div>
              ) : (
                <div className="grid gap-2">
                  <Label htmlFor="scroll_amount">Scroll amount</Label>
                  <Input
                    id="scroll_amount"
                    type="number"
                    inputMode="numeric"
                    placeholder="150"
                    {...methods.register('scroll_amount')}
                  />
                  <p className="text-muted-foreground text-xs">Use a signed number. Positive scrolls down; negative scrolls up.</p>
                </div>
              )}
            </>
          ) : null}
          {isMarkerStep ? (
            <div className="grid gap-2">
              <Label>Recorded marker</Label>
              <pre className="border-border/60 bg-muted/20 max-h-40 overflow-auto rounded-md border p-2 font-mono text-[11px] leading-5 text-muted-foreground">
                {JSON.stringify(step.action_payload || {}, null, 2)}
              </pre>
            </div>
          ) : null}
          {!isMarkerStep && actionHasValue ? (
            <div className="grid gap-2">
              <Label htmlFor="value" className="text-sm font-medium">{actionValueLabel(step)}</Label>
              <Input
                id="value"
                type={isWaitStep ? 'number' : 'text'}
                inputMode={isWaitStep ? 'numeric' : undefined}
                disabled={!canEdit('value')}
                placeholder={isWaitStep ? '1000' : isScreenshotStep ? '' : undefined}
                className="h-[46px] rounded-[10px] text-[15px]"
                {...methods.register('value')}
              />
              {methods.formState.errors.value ? (
                <p className="text-destructive text-xs">{methods.formState.errors.value.message}</p>
              ) : null}
            </div>
          ) : null}

          {isExtractStep ? (
            <>
              <div className="grid gap-2">
                <Label htmlFor="extract_source">What to read</Label>
                <select id="extract_source" className={fieldSelectClass} {...methods.register('extract_source')}>
                  <option value="text">The text shown on the page</option>
                  <option value="value">The value in a field</option>
                  <option value="attribute">An attribute of the element (for example a link address)</option>
                </select>
              </div>
              {extractSource === 'attribute' ? (
                <div className="grid gap-2">
                  <Label htmlFor="extract_attribute">Attribute name</Label>
                  <Input id="extract_attribute" type="text" placeholder="e.g., href" {...methods.register('extract_attribute')} />
                </div>
              ) : null}
              <div className="grid gap-2">
                <Label htmlFor="extract_pattern">Keep only this part (optional)</Label>
                <Input id="extract_pattern" type="text" placeholder="e.g., #([A-Z]-\d+)" {...methods.register('extract_pattern')} />
                <p className="text-muted-foreground text-xs">
                  A pattern. The first bracketed part is kept, or the whole match when there are no brackets. The run fails clearly if nothing matches.
                </p>
              </div>
            </>
          ) : null}
          {canHaveCondition ? (
            <div className="grid gap-2">
              <Label htmlFor="run_if_input" className="text-sm font-medium">Run this step only when (optional)</Label>
              <div className="flex flex-wrap items-center gap-2">
                <Input id="run_if_input" type="text" placeholder="input name" className="w-40" {...methods.register('run_if_input')} />
                <select aria-label="Condition" className={fieldSelectClass} {...methods.register('run_if_op')}>
                  <option value="not_empty">has a value</option>
                  <option value="equals">equals</option>
                  <option value="not_equals">does not equal</option>
                </select>
                {runIfOp === 'equals' || runIfOp === 'not_equals' ? (
                  <Input aria-label="Compared value" type="text" placeholder="value" className="w-40" {...methods.register('run_if_value')} />
                ) : null}
              </div>
              <p className="text-muted-foreground text-xs">
                Looks only at what the person running it supplied, never at the page. Leave the input name empty to always run.
              </p>
            </div>
          ) : null}
          {isBoundStep && canHaveCondition ? (
            <div className="grid gap-2">
              <Label htmlFor="on_missing">If the item isn’t on the page</Label>
              <select id="on_missing" className={fieldSelectClass} {...methods.register('on_missing')}>
                <option value="">Stop with an error</option>
                <option value="skip">Skip this step and carry on</option>
                <option value="ask">Ask a person (needs a visible browser)</option>
              </select>
              <p className="text-muted-foreground text-xs">
                Only used when the list is on the page and the item clearly isn’t in it. It never acts on a different row.
              </p>
            </div>
          ) : null}

      {!hideSelectorTools && !isAiReviewStep && canEdit('validation') ? (
        <StepValidationPanel
          step={step}
          assertions={assertions}
          onChange={(next) => methods.setValue('assertions', next, { shouldDirty: true })}
        />
      ) : null}

      {!isMarkerStep ? (
      <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
        <CollapsibleTrigger asChild>
          <button type="button" className="flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-300">
            Advanced
            <ChevronRight className={cn('size-3.5 transition-transform', advancedOpen && 'rotate-90')} aria-hidden />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-2 pt-3">
      {actionHasSelectors && !hideSelectorTools ? (
        <div className="grid gap-2">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="selector_0">Selectors</Label>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!canEdit('selectors')}
              onClick={() => methods.setValue('selectors', [...selectors, ''], { shouldDirty: true })}
            >
              Add selector
            </Button>
          </div>
          <p className="text-muted-foreground text-xs">
            Top selector is primary. Selectors below are fallbacks.
          </p>
          <div className="space-y-2">
            {selectors.map((_, index) => (
              <div key={`selector-${index}`} className="flex items-center gap-2">
                <Input
                  id={`selector_${index}`}
                  type="text"
                  placeholder={index === 0 ? 'Primary selector' : `Fallback selector ${index}`}
                  disabled={!canEdit('selectors')}
                  {...methods.register(`selectors.${index}` as const)}
                />
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  className="text-destructive hover:text-destructive h-7 w-7"
                  disabled={!canEdit('selectors') || selectors.length <= 1}
                  onClick={() =>
                    methods.setValue(
                      'selectors',
                      selectors.filter((_, i) => i !== index),
                      { shouldDirty: true },
                    )
                  }
                  aria-label={`Remove selector ${index + 1}`}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {showSelectorAndAnchorTools ? (
      <>
      <Card className={cn('gap-2 py-3', PANEL_CARD_CLASS)}>
        <CardHeader className="p-2.5 pb-1">
          <CardTitle className="text-base font-semibold">Selector channels</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2 p-2.5 pt-0">
          <div className="grid gap-1.5">
            <Label htmlFor="css">CSS</Label>
            <Input id="css" disabled={!canEdit('selectors')} {...methods.register('css')} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="aria">ARIA</Label>
            <Input
              id="aria"
              disabled={!canEdit('selectors')}
              {...methods.register('aria')}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="text_based">Text-based</Label>
            <Input
              id="text_based"
              disabled={!canEdit('selectors')}
              {...methods.register('text_based')}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="xpath">XPath</Label>
            <Input
              id="xpath"
              disabled={!canEdit('selectors')}
              {...methods.register('xpath')}
            />
          </div>
        </CardContent>
      </Card>

      {frameChain.length > 0 ? (
        <Card className={cn('gap-2 py-3', PANEL_CARD_CLASS)}>
          <CardHeader className="p-2.5 pb-1">
            <CardTitle className="text-base font-semibold">Frame context</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 p-2.5 pt-0">
            {frameChain.map((frame, index) => (
              <div key={`frame-${index}`} className="border-border/60 bg-muted/20 rounded-md border p-2">
                <p className="truncate font-mono text-[11px]">{String(frame.selector || '')}</p>
                {Array.isArray(frame.fallback_selectors) && frame.fallback_selectors.length > 0 ? (
                  <p className="text-muted-foreground mt-1 truncate font-mono text-[11px]">
                    {frame.fallback_selectors.map((item) => String(item)).join(' | ')}
                  </p>
                ) : null}
                {frame.url_pattern ? (
                  <p className="text-muted-foreground mt-1 truncate font-mono text-[11px]">
                    {String(frame.url_pattern)}
                  </p>
                ) : null}
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}

      <Card className={cn('gap-2 py-3', PANEL_CARD_CLASS)}>
        <CardHeader className="p-2.5 pb-1">
          <CardTitle className="text-base font-semibold">Anchors</CardTitle>
          <CardDescription className="text-xs">Use format `relation:element`; above/below describes the target relative to that anchor.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2.5 p-2.5 pt-0">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="anchor_0">Anchors</Label>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!canEdit('anchors')}
              onClick={() => methods.setValue('anchors', [...anchors, ''], { shouldDirty: true })}
            >
              Add anchor
            </Button>
          </div>
          <div className="space-y-1.5">
            {anchors.map((_, index) => (
              <div key={`anchor-${index}`} className="flex items-center gap-1.5">
                <Input
                  id={`anchor_${index}`}
                  type="text"
                  placeholder={index === 0 ? 'near:Sign in' : `Anchor ${index + 1}`}
                  disabled={!canEdit('anchors')}
                  {...methods.register(`anchors.${index}` as const)}
                />
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  className="text-destructive hover:text-destructive h-7 w-7"
                  disabled={!canEdit('anchors') || anchors.length <= 1}
                  onClick={() =>
                    methods.setValue(
                      'anchors',
                      anchors.filter((_, i) => i !== index),
                      { shouldDirty: true },
                    )
                  }
                  aria-label={`Remove anchor ${index + 1}`}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
      </>
      ) : null}

      <ElementFingerprintCard fingerprint={step.fingerprint} />
        </CollapsibleContent>
      </Collapsible>
      ) : null}

      {!hideSubmitButton ? (
        <>
          <Separator />
          <div className="flex items-center justify-end gap-2.5">
            {assertionsInvalid ? (
              <p className="text-destructive text-xs">Fill in the missing checks above before saving.</p>
            ) : null}
            <Button type="submit" size="default" disabled={methods.formState.isSubmitting || isMarkerStep || assertionsInvalid}>
              {methods.formState.isSubmitting ? 'Saving…' : 'Save step'}
            </Button>
          </div>
        </>
      ) : null}
      {methods.formState.errors.root ? (
        <p className="text-destructive text-sm">{(methods.formState.errors.root as { message?: string }).message}</p>
      ) : null}
        </form>
      </FormProvider>
  )
}))
