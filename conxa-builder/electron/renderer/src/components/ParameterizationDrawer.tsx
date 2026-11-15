import { useMemo, useRef, useState } from 'react'
import { Trash2, ChevronDown, ChevronRight } from 'lucide-react'
import type { StepEditorDTO, WorkflowResponse } from '../types/workflow'
import { fetchWorkflow, patchSkillInputs, postWorkflowReplaceLiterals } from '../api/workflowApi'
import { Button } from '@/components/ui/button'
import { fieldTextareaClass, SECTION_LABEL } from '@/lib/fieldStyles'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import { InfoHint } from '@/components/ui/info-hint'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'
import {
  addSpottedToRows,
  collectVariableIdsFromSteps,
  isEffectivelyOptional,
  labelFromId,
  missingSpottedIds,
  newEmptyRow,
  normalizeVariablePlaceholder,
  type VariableFormRow,
  rowsFromServerInputs,
  rowsToServerPayload,
  stepsUsingVariable,
} from '@/lib/skillInputVariables'

type Props = {
  workflow: WorkflowResponse
  onSaved: (w: WorkflowResponse) => void
  onClose: () => void
}

// grid-cols shared by an expanded card's field labels and its inputs so columns stay aligned.
const ROW_COLS = 'grid grid-cols-[minmax(0,1.2fr)_minmax(0,1.2fr)_128px_minmax(0,1fr)] gap-2.5'
const ROW_GRID = `${ROW_COLS} items-center`
const ROW_HEAD = `${ROW_COLS} text-muted-foreground text-xs font-medium`

/** The product's own `{{id}}` syntax, worn by the input itself. A pasted `{{name}}` is
 * unwrapped on the way in so the field never ends up displaying doubled braces. */
function VariableNameField({
  id,
  value,
  onChange,
  placeholder,
  ariaLabel,
}: {
  id?: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  ariaLabel?: string
}) {
  return (
    <div className="relative">
      <span
        aria-hidden
        className="text-brand/70 pointer-events-none absolute top-1/2 left-2 -translate-y-1/2 font-mono text-sm select-none"
      >
        {'{{'}
      </span>
      <Input
        id={id}
        aria-label={ariaLabel}
        className="h-8 px-6.5 font-mono text-sm"
        placeholder={placeholder}
        value={value}
        onChange={(e) => {
          const raw = e.target.value
          const unwrapped = /^\{\{.*\}\}$/.test(raw.trim())
            ? raw.trim().replace(/^\{\{\s*/, '').replace(/\s*\}\}$/, '')
            : raw
          onChange(unwrapped)
        }}
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
      />
      <span
        aria-hidden
        className="text-brand/70 pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 font-mono text-sm select-none"
      >
        {'}}'}
      </span>
    </div>
  )
}

const TYPE_LABELS: Record<VariableFormRow['varType'], string> = {
  text: 'Text',
  select: 'Choice list',
  multiselect: 'Multiple choice',
  date: 'Date',
  rows: 'Table (one row per repeat)',
}

/** Collapsed summary of one variable; clicking it expands the full editor in place. */
function VariableCard({
  row,
  usingSteps,
  expanded,
  onToggle,
  onChange,
  onRemove,
}: {
  row: VariableFormRow
  usingSteps: StepEditorDTO[]
  expanded: boolean
  onToggle: () => void
  onChange: (r: VariableFormRow) => void
  onRemove: () => void
}) {
  const usage =
    usingSteps.length === 0 ? (
      <span className="text-status-warn shrink-0 text-xs">Not used in any step</span>
    ) : (
      <span className="shrink-0 truncate text-xs text-zinc-500">
        Used in: {usingSteps.map((s) => `Step ${s.step_index + 1}`).join(', ')}
      </span>
    )
  return (
    <div className="rounded-xl border border-white/12" data-slot="var-row">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        className="flex w-full flex-col gap-3 rounded-xl p-4 text-left hover:bg-white/[0.03] focus-visible:ring-2 focus-visible:ring-brand-ring focus-visible:outline-none"
      >
        <span className="flex w-full items-center justify-between gap-3">
          <span className="flex min-w-0 items-center gap-2.5">
            <span className="truncate font-mono text-base text-zinc-100">{row.id || 'unnamed'}</span>
            {row.label ? <span className="truncate text-sm text-zinc-500">{row.label}</span> : null}
          </span>
          {usage}
        </span>
        {!expanded ? (
          <span className="flex flex-wrap gap-x-5 gap-y-1 text-sm text-zinc-500">
            <span>
              Type <span className="text-zinc-200">{TYPE_LABELS[row.varType]}</span>
            </span>
            <span>
              Default <span className="text-zinc-200">{row.defaultValue.trim() || 'none'}</span>
            </span>
            <span>
              Optional <span className="text-zinc-200">{isEffectivelyOptional(row) ? 'Yes' : 'No'}</span>
            </span>
            <span>
              Sensitive <span className="text-zinc-200">{row.sensitive ? 'Yes' : 'No'}</span>
            </span>
          </span>
        ) : null}
      </button>
      {expanded ? (
        <div className="px-4 pb-4">
          <VariableEditor row={row} onChange={onChange} onRemove={onRemove} />
        </div>
      ) : null}
    </div>
  )
}

function VariableEditor({
  row,
  onChange,
  onRemove,
}: {
  row: VariableFormRow
  onChange: (r: VariableFormRow) => void
  onRemove: () => void
}) {
  const optionalLocked = row.defaultValue.trim() !== ''
  const optionalChecked = isEffectivelyOptional(row)
  return (
    <div className="space-y-2">
      <div className={ROW_HEAD}>
        <span>Name</span>
        <span>Label</span>
        <span>Type</span>
        <span>Default</span>
      </div>
      <div className={ROW_GRID}>
        <VariableNameField
          ariaLabel="Variable name"
          placeholder="email"
          value={row.id}
          onChange={(id) => {
            const next: VariableFormRow = { ...row, id }
            if (!row.label.trim() || row.label === labelFromId(row.id) || !row.id) {
              next.label = labelFromId(id)
            }
            onChange(next)
          }}
        />
        <Input
          aria-label="Display label"
          className="h-8 text-sm"
          placeholder="Work email"
          value={row.label}
          onChange={(e) => onChange({ ...row, label: e.target.value })}
        />
        <Select
          value={row.varType}
          onValueChange={(v) =>
            onChange({
              ...row,
              varType:
                v === 'select' ? 'select' : v === 'multiselect' ? 'multiselect' : v === 'date' ? 'date' : v === 'rows' ? 'rows' : 'text',
            })
          }
        >
          <SelectTrigger size="sm" className="h-8 w-full text-sm" aria-label="Type">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="text">Text</SelectItem>
            <SelectItem value="select">Choice list</SelectItem>
            <SelectItem value="multiselect">Multiple choice</SelectItem>
            <SelectItem value="date">Date</SelectItem>
            <SelectItem value="rows">Table (one row per repeat)</SelectItem>
          </SelectContent>
        </Select>
        <Input
          aria-label="Default value"
          type={row.varType === 'date' ? 'date' : 'text'}
          className="h-8 text-sm"
          placeholder={
            row.varType === 'select'
              ? 'must match an option'
              : row.varType === 'multiselect'
                ? 'comma-separated options'
                : 'none'
          }
          value={row.defaultValue}
          onChange={(e) => onChange({ ...row, defaultValue: e.target.value })}
        />
      </div>
      {row.varType === 'rows' ? (
        <div className="flex items-center gap-2">
          <Label className="text-muted-foreground shrink-0 text-xs">Columns</Label>
          <Input
            className="h-8 text-sm"
            placeholder="to, subject"
            aria-label="Column names"
            value={row.columnsText}
            onChange={(e) => onChange({ ...row, columnsText: e.target.value })}
          />
        </div>
      ) : null}
      {row.varType === 'select' || row.varType === 'multiselect' ? (
        <div className="flex items-center gap-2">
          <Label className="text-muted-foreground shrink-0 text-xs">Options</Label>
          <Input
            className="h-8 text-sm"
            placeholder="small, medium, large"
            value={row.optionsText}
            onChange={(e) => onChange({ ...row, optionsText: e.target.value })}
          />
        </div>
      ) : null}
      <div className="flex items-center gap-5 pt-1 text-sm text-zinc-400">
        <label className="flex items-center gap-2">
          <Checkbox
            checked={optionalChecked}
            disabled={optionalLocked}
            onCheckedChange={(checked) => onChange({ ...row, optional: checked === true })}
          />
          Optional{optionalLocked ? <span className="text-xs text-zinc-500">(auto — has a default)</span> : null}
          <InfoHint
            size="sm"
            label="Optional"
            summary="The skill can run without this value. A variable with a default is always optional — the runtime fills the default in automatically."
          />
        </label>
        <label className="flex items-center gap-2">
          <Checkbox checked={row.sensitive} onCheckedChange={(checked) => onChange({ ...row, sensitive: checked === true })} />
          Sensitive
          <InfoHint
            size="sm"
            label="Sensitive"
            summary="Blanks this value out of saved test history, and helps pick the shipped bundle's authentication type from the variable's name."
          />
        </label>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="text-muted-foreground hover:text-destructive ml-auto gap-1.5"
          onClick={onRemove}
          aria-label={`Remove ${row.id || 'this variable'}`}
        >
          <Trash2 className="size-3.5" />
          Remove
        </Button>
      </div>
    </div>
  )
}

export function ParameterizationInlinePanel({ workflow, onSaved, onClose }: Props) {
  // Computed once at mount from the server-loaded workflow, then never recomputed from props —
  // `workflow` can change under us after a successful "Replace everywhere" call (it re-fetches
  // and calls onSaved), and re-deriving rows from that would blow away any unsaved edits sitting
  // in the form (audit finding H3). A fresh ParameterizationInlinePanel mount (new skill_id, see
  // HumanEditPage's `key`) gets a fresh computation because it's a brand new component instance.
  const initial = useRef<{ rows: VariableFormRow[]; jsonDraft: string } | null>(null)
  if (initial.current === null) {
    const fromServer = rowsFromServerInputs(workflow.inputs)
    const spotted = collectVariableIdsFromSteps(workflow.steps)
    if (fromServer.length === 0 && spotted.length > 0) {
      const withSpotted = addSpottedToRows([], spotted)
      const payload = rowsToServerPayload(withSpotted)
      initial.current = { rows: withSpotted, jsonDraft: JSON.stringify(payload.ok ? payload.data : [], null, 2) }
    } else {
      initial.current = { rows: fromServer, jsonDraft: JSON.stringify(workflow.inputs, null, 2) }
    }
  }

  const [rows, setRows] = useState<VariableFormRow[]>(initial.current.rows)
  // Cards start collapsed to a one-line summary; rows the user just added open straight away.
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => new Set())
  const expand = (keys: string[]) => setExpandedKeys((prev) => new Set([...prev, ...keys]))
  const toggleExpanded = (key: string) =>
    setExpandedKeys((prev) => {
      const next = new Set(prev)
      if (!next.delete(key)) next.add(key)
      return next
    })
  const [err, setErr] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [jsonDraft, setJsonDraft] = useState(initial.current.jsonDraft)
  const [jsonErr, setJsonErr] = useState<string | null>(null)
  const [replaceFind, setReplaceFind] = useState('')
  const [replaceVariable, setReplaceVariable] = useState('')
  const [replaceBusy, setReplaceBusy] = useState(false)
  const [replaceErr, setReplaceErr] = useState<string | null>(null)
  const [replaceInfo, setReplaceInfo] = useState<string | null>(null)

  const spottedIds = useMemo(() => collectVariableIdsFromSteps(workflow.steps), [workflow.steps])
  const missing = useMemo(() => missingSpottedIds(spottedIds, rows), [spottedIds, rows])
  const usageByRowId = useMemo(
    () => new Map(rows.map((r) => [r.key, stepsUsingVariable(workflow.steps, r.id)])),
    [rows, workflow.steps],
  )

  // Counts rows that differ from (or are missing from) what the server has saved, so the
  // footer can say something more useful than a bare "Save" prompt.
  const changedCount = useMemo(() => {
    const strip = ({ key: _key, ...rest }: VariableFormRow) => rest
    const saved = new Map(
      rowsFromServerInputs(workflow.inputs).map((r) => [r.id.trim().toLowerCase(), JSON.stringify(strip(r))]),
    )
    const seen = new Set<string>()
    let n = 0
    for (const r of rows) {
      const id = r.id.trim().toLowerCase()
      if (!id) continue
      seen.add(id)
      if (saved.get(id) !== JSON.stringify(strip(r))) n++
    }
    for (const id of saved.keys()) if (!seen.has(id)) n++
    return n
  }, [rows, workflow.inputs])

  const applyJsonToForm = () => {
    setJsonErr(null)
    let parsed: unknown
    try {
      parsed = JSON.parse(jsonDraft) as unknown
    } catch {
      setJsonErr('Not valid JSON')
      return
    }
    if (!Array.isArray(parsed)) {
      setJsonErr('JSON must be an array of variable objects')
      return
    }
    setRows(rowsFromServerInputs((parsed as Record<string, unknown>[]).map((o) => (typeof o === 'object' && o ? o : {}))))
  }

  const copyFormToJson = () => {
    const p = rowsToServerPayload(rows)
    if (p.ok) {
      setJsonDraft(JSON.stringify(p.data, null, 2))
      setJsonErr(null)
    } else {
      setJsonErr(p.error)
    }
  }

  const replaceLiteralInWorkflow = () => {
    setReplaceErr(null)
    setReplaceInfo(null)
    const find = replaceFind.trim()
    if (!find) {
      setReplaceErr('Enter the exact text to find (for example conxa-db).')
      return
    }
    const ph = normalizeVariablePlaceholder(replaceVariable)
    if (!ph.ok) {
      setReplaceErr(ph.error)
      return
    }
    setReplaceBusy(true)
    postWorkflowReplaceLiterals(workflow.skill_id, { find, replace_with: ph.value })
      .then(({ workflow: next, match_count }) => {
        onSaved(next)
        if (match_count > 0) {
          setReplaceInfo(`Replaced ${match_count} occurrence${match_count === 1 ? '' : 's'}.`)
          setReplaceFind('')
          setReplaceVariable('')
        } else {
          setReplaceInfo(`No matches for "${find}" in any step's typed value.`)
        }
      })
      .catch((e: Error) => setReplaceErr(e.message))
      .finally(() => setReplaceBusy(false))
  }

  const save = () => {
    setErr(null)
    const out = rowsToServerPayload(rows)
    if (!out.ok) {
      setErr(out.error)
      return
    }
    setSaving(true)
    patchSkillInputs(workflow.skill_id, { inputs: out.data })
      .then(() => fetchWorkflow(workflow.skill_id))
      .then((w: WorkflowResponse) => {
        onSaved(w)
        onClose()
      })
      .catch((e: Error) => setErr(e.message))
      .finally(() => setSaving(false))
  }

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col">
      {/* Plain overflow div, not ScrollArea: this dialog is auto-height (max-h only), where
          ScrollArea's size-full viewport never gets a definite height and would clip. */}
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-7 py-6">
        {spottedIds.length > 0 ? (
          <div className="flex flex-col gap-2.5" data-slot="spotted">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className={SECTION_LABEL}>Used in your steps</p>
              {missing.length > 0 ? (
                <button
                  type="button"
                  className="text-brand text-sm font-medium hover:underline"
                  onClick={() => {
                    const next = addSpottedToRows(rows, missing)
                    expand(next.filter((r) => !rows.includes(r)).map((r) => r.key))
                    setRows(next)
                  }}
                >
                  + Add {missing.length === 1 ? `{{${missing[0]}}}` : `${missing.length} variables`}
                </button>
              ) : null}
            </div>
            <div className="flex flex-wrap gap-2">
              {spottedIds.map((id) => {
                // Case-insensitive to match the "Add N" count and dedup, which treat
                // {{Email}} and {{email}} as the same binding (audit finding L-4).
                const has = rows.some((r) => r.id.trim().toLowerCase() === id.toLowerCase())
                return (
                  <span
                    key={id}
                    title={has ? 'Linked to a variable below' : 'Not a variable yet'}
                    className={cn(
                      'rounded-full px-2.5 py-1 font-mono text-sm',
                      has ? 'bg-brand/12 text-brand' : 'border-brand/50 text-brand border border-dashed',
                    )}
                  >
                    {id}
                  </span>
                )
              })}
            </div>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm leading-relaxed">
            Type <code className="bg-muted/60 rounded px-0.5">{'{{name}}'}</code> in any step field to create a variable.
          </p>
        )}

        <div className={cn('flex flex-col gap-2.5', spottedIds.length > 0 && 'border-t border-white/8 pt-5')}>
          <div className="flex items-center justify-between">
            <p className={SECTION_LABEL}>Variables</p>
            <button
              type="button"
              className="text-brand text-sm font-medium hover:underline"
              onClick={() => {
                const row = newEmptyRow()
                expand([row.key])
                setRows((r) => [...r, row])
              }}
            >
              + New variable
            </button>
          </div>
          {rows.length > 0 ? (
            rows.map((row, i) => (
              <VariableCard
                key={row.key}
                row={row}
                usingSteps={usageByRowId.get(row.key) ?? []}
                expanded={expandedKeys.has(row.key)}
                onToggle={() => toggleExpanded(row.key)}
                onChange={(next) =>
                  setRows((prev) => {
                    const c = [...prev]
                    c[i] = next
                    return c
                  })
                }
                onRemove={() => setRows((prev) => prev.filter((_, j) => j !== i))}
              />
            ))
          ) : (
            <p className="text-muted-foreground py-2 text-sm">No variables yet.</p>
          )}
        </div>

        <div className="flex flex-col gap-2.5 border-t border-white/8 pt-5">
          <h3 className="text-base font-semibold">Turn a recorded value into a variable</h3>
          <p className="text-muted-foreground text-sm leading-normal">
            Replaces a literal value inside typed step values only, selectors are left untouched.
          </p>
          <div className="flex items-center gap-3">
            <Input
              id="param-replace-find"
              aria-label="Find this text in your steps"
              className="h-[42px] flex-1 font-mono text-sm"
              value={replaceFind}
              onChange={(e) => {
                setReplaceFind(e.target.value)
                setReplaceErr(null)
              }}
              spellCheck={false}
              placeholder="conxa-db"
              autoCapitalize="off"
              autoCorrect="off"
            />
            <span className="text-sm text-zinc-500">to</span>
            <div className="flex-1">
              <VariableNameField
                id="param-replace-var"
                ariaLabel="Replace it with variable"
                value={replaceVariable}
                onChange={(v) => {
                  setReplaceVariable(v)
                  setReplaceErr(null)
                }}
                placeholder="db_name"
              />
            </div>
          </div>
          {replaceErr ? <p className="text-destructive text-sm">{replaceErr}</p> : null}
          {replaceInfo ? <p className="text-muted-foreground text-sm">{replaceInfo}</p> : null}
          <div>
            <Button type="button" variant="outline" disabled={replaceBusy} onClick={() => void replaceLiteralInWorkflow()}>
              {replaceBusy ? 'Replacing…' : 'Replace everywhere'}
            </Button>
          </div>
        </div>

        <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
          <CollapsibleTrigger className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-xs font-medium" type="button">
            {advancedOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
            Advanced: edit as JSON
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="mt-2 space-y-2">
              <textarea
                className={cn(fieldTextareaClass, 'font-mono min-h-48 w-full text-xs')}
                value={jsonDraft}
                onChange={(e) => {
                  setJsonDraft(e.target.value)
                  setJsonErr(null)
                }}
                spellCheck={false}
                aria-label="Variables JSON"
              />
              {jsonErr ? <p className="text-destructive text-sm">{jsonErr}</p> : null}
              <div className="flex flex-wrap gap-2">
                <Button type="button" size="sm" variant="secondary" onClick={applyJsonToForm}>
                  Apply JSON to form
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={copyFormToJson}>
                  Load form into JSON
                </Button>
              </div>
            </div>
          </CollapsibleContent>
        </Collapsible>
      </div>
      {err ? <p className="text-destructive shrink-0 px-7 pt-2 text-sm">{err}</p> : null}
      <div className="flex shrink-0 items-center justify-between gap-2 border-t border-white/8 px-7 py-4">
        <p className="text-muted-foreground text-sm">
          {changedCount > 0 ? `${changedCount} unsaved change${changedCount === 1 ? '' : 's'}` : 'All changes saved'}
        </p>
        <Button type="button" variant="brand" disabled={saving} onClick={save}>
          {saving ? 'Saving…' : 'Save variables'}
        </Button>
      </div>
    </div>
  )
}
