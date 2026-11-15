import type { StepEditorDTO } from '../types/workflow'

/** Must match conxa_compile/editor/placeholder_grammar.py PLACEHOLDER_RE and
 * runtime/run.js's interpolate() exactly — three independent scanners, one grammar
 * (audit finding C3). Whitespace inside the braces (`{{ id }}`) is tolerated by all three.
 * `{{row.to}}` (one dot) is a column of the current row of a table loop, never an input. */
const PLACEHOLDER = /\{\{\s*([a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*)?)\s*\}\}/g

const ID_PATTERN = /^[a-zA-Z][a-zA-Z0-9_]*$/

function collectStringsFromValue(v: unknown, out: string[]): void {
  if (v == null) return
  if (typeof v === 'string') {
    out.push(v)
    return
  }
  if (Array.isArray(v)) {
    for (const x of v) collectStringsFromValue(x, out)
    return
  }
  if (typeof v === 'object') {
    for (const x of Object.values(v as object)) collectStringsFromValue(x, out)
  }
}

export function collectVariableIdsFromSteps(steps: StepEditorDTO[]): string[] {
  const set = new Set<string>()
  for (const step of steps) {
    for (const b of step.parameter_bindings ?? []) {
      const rec = b as { variable_id?: string }
      if (rec.variable_id && !rec.variable_id.includes('.')) set.add(rec.variable_id)
    }
    const blob: string[] = []
    collectStringsFromValue(step, blob)
    for (const s of blob) {
      for (const m of s.matchAll(PLACEHOLDER)) {
        if (m[1] && !m[1].includes('.')) set.add(m[1])
      }
    }
  }
  return [...set].sort((a, b) => a.localeCompare(b))
}

/** Steps (including nested branch steps) that reference this variable id, via either an
 * explicit parameter binding or a `{{id}}` placeholder anywhere in the step's fields. */
export function stepsUsingVariable(steps: StepEditorDTO[], id: string): StepEditorDTO[] {
  const idKey = id.trim().toLowerCase()
  if (!idKey) return []
  const out: StepEditorDTO[] = []
  const visit = (list: StepEditorDTO[]) => {
    for (const step of list) {
      const bound = (step.parameter_bindings ?? []).some(
        (b) => String((b as { variable_id?: string }).variable_id ?? '').toLowerCase() === idKey,
      )
      let placed = false
      if (bound) {
        out.push(step)
        placed = true
      }
      if (!placed) {
        // Exclude branch_steps here — nested steps are walked separately below, and including
        // them would misattribute a variable used only inside a branch to its parent step too.
        const { branch_steps: _branch, ...rest } = step
        const blob: string[] = []
        collectStringsFromValue(rest, blob)
        for (const s of blob) {
          if ([...s.matchAll(PLACEHOLDER)].some((m) => m[1]?.toLowerCase() === idKey)) {
            out.push(step)
            break
          }
        }
      }
      if (step.branch_steps?.length) visit(step.branch_steps)
    }
  }
  visit(steps)
  return out
}

export function labelFromId(id: string): string {
  if (!id.trim()) return ''
  return id
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .split(' ')
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ')
}

export type InputVariableType = 'text' | 'select' | 'date' | 'multiselect' | 'rows'

export type VariableFormRow = {
  key: string
  id: string
  label: string
  varType: InputVariableType
  optionsText: string
  /** Comma-separated column names of a table ('rows') input, e.g. "to, subject". */
  columnsText: string
  defaultValue: string
  sensitive: boolean
  optional: boolean
}

/** A variable carrying a default is optional whether or not the box is ticked — the runtime
 * fills the default in before its required-input gate runs (runtime/server.js). Single source
 * of truth for the checkbox's checked+disabled state and for what gets saved. */
export function isEffectivelyOptional(row: VariableFormRow): boolean {
  return row.optional || row.defaultValue.trim() !== ''
}

/** Accepts `db_name` or full `{{db_name}}` for bulk replace / placeholders. */
export function normalizeVariablePlaceholder(raw: string): { ok: true; value: string } | { ok: false; error: string } {
  const t = raw.trim()
  if (!t) {
    return { ok: false, error: 'Enter a variable id (e.g. db_name) or paste {{db_name}}.' }
  }
  const fullMatch = /^\{\{([a-zA-Z][a-zA-Z0-9_]*)\}\}$/.exec(t)
  if (fullMatch?.[1]) {
    return { ok: true, value: `{{${fullMatch[1]}}}` }
  }
  if (!ID_PATTERN.test(t)) {
    return {
      ok: false,
      error: 'Invalid id. Use letters, numbers, underscore; start with a letter (same as in {{id}}).',
    }
  }
  return { ok: true, value: `{{${t}}}` }
}

export function newEmptyRow(): VariableFormRow {
  return {
    key: `new-${globalThis.crypto?.randomUUID?.() ?? String(Date.now())}`,
    id: '',
    label: '',
    varType: 'text',
    optionsText: '',
    columnsText: '',
    defaultValue: '',
    sensitive: false,
    optional: false,
  }
}

export function rowsFromServerInputs(inputs: Record<string, unknown>[]): VariableFormRow[] {
  return inputs.map((raw, i) => {
    const id = String(raw.id ?? '')
    return {
      key: `row-${i}`,
      id,
      label: String(raw.label ?? (id ? labelFromId(id) : '')),
      varType:
        raw.type === 'select'
          ? 'select'
          : raw.type === 'multiselect'
            ? 'multiselect'
            : raw.type === 'date'
              ? 'date'
              : raw.type === 'rows'
                ? 'rows'
                : 'text',
      optionsText: Array.isArray(raw.options) ? (raw.options as unknown[]).map((o) => String(o)).join(', ') : '',
      columnsText: Array.isArray(raw.columns) ? (raw.columns as unknown[]).map((c) => String(c)).join(', ') : '',
      defaultValue: raw.default == null ? '' : String(raw.default),
      sensitive: raw.sensitive === true,
      optional: raw.optional === true,
    }
  })
}

export function rowsToServerPayload(
  rows: VariableFormRow[],
): { ok: true; data: Record<string, unknown>[] } | { ok: false; error: string } {
  // Ids are compared case-insensitively so {{Email}} and {{email}} can't silently collide as
  // two distinct-looking-but-runtime-identical bindings (audit finding L3).
  const byId = new Set<string>()
  const data: Record<string, unknown>[] = []
  for (const row of rows) {
    const id = row.id.trim()
    if (!id) continue
    const idKey = id.toLowerCase()
    if (byId.has(idKey)) {
      return { ok: false, error: `Duplicate variable id: ${id}` }
    }
    if (!ID_PATTERN.test(id)) {
      return {
        ok: false,
        error: `Invalid id "${id}". Use letters, numbers, underscore; start with a letter (same as in {{id}}).`,
      }
    }
    byId.add(idKey)
    const rec: Record<string, unknown> = {
      id,
      label: row.label.trim() || labelFromId(id),
      type: row.varType,
      default: null,
    }
    if (row.varType === 'select') {
      const options = row.optionsText
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
      rec.options = options
      const def = row.defaultValue.trim()
      if (def) {
        if (!options.includes(def)) {
          return { ok: false, error: `Default "${def}" for "${id}" must be one of its options.` }
        }
        rec.default = def
      }
    } else if (row.varType === 'multiselect') {
      const options = row.optionsText
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
      rec.options = options
      const def = row.defaultValue.trim()
      if (def) {
        // Comma-joined labels, same as optionsText — matches editor/dto.py's
        // SkillInputVariable.default shape for a multiselect (a single flat string field for
        // every input type, never a separate list-typed one).
        const picks = def.split(',').map((s) => s.trim()).filter(Boolean)
        const unknown = picks.find((p) => !options.includes(p))
        if (unknown) {
          return { ok: false, error: `Default "${unknown}" for "${id}" must be one of its options.` }
        }
        rec.default = def
      }
    } else if (row.varType === 'rows') {
      const columns = row.columnsText.split(',').map((s) => s.trim()).filter(Boolean)
      if (!columns.length || columns.length > 12) {
        return { ok: false, error: `"${id}" is a table: name between 1 and 12 columns, separated by commas.` }
      }
      const bad = columns.find((c) => !ID_PATTERN.test(c))
      if (bad) {
        return { ok: false, error: `Invalid column name "${bad}". Use letters, numbers, underscore; start with a letter.` }
      }
      if (new Set(columns).size !== columns.length) {
        return { ok: false, error: `"${id}" lists the same column twice.` }
      }
      rec.options = []
      rec.columns = columns
    } else {
      rec.options = []
      const def = row.defaultValue.trim()
      if (def) rec.default = def
    }
    if (row.sensitive) rec.sensitive = true
    // Persisted only when it changes the outcome, keeping the stored shape sparse (as with
    // `sensitive`). A defaulted variable is already optional at the gate, so the flag would be
    // redundant noise there.
    if (row.optional && !row.defaultValue.trim()) rec.optional = true
    data.push(rec)
  }
  return { ok: true, data }
}

export function missingSpottedIds(spotted: string[], rows: VariableFormRow[]): string[] {
  const have = new Set(rows.map((r) => r.id.trim().toLowerCase()).filter(Boolean))
  return spotted.filter((id) => !have.has(id.toLowerCase()))
}

export function addSpottedToRows(
  rows: VariableFormRow[],
  ids: string[],
): VariableFormRow[] {
  if (ids.length === 0) return rows
  const additions = ids.map((id) => ({
    key: `spot-${id}-${globalThis.crypto?.randomUUID?.() ?? id}`,
    id,
    label: labelFromId(id),
    varType: 'text' as const,
    optionsText: '',
    columnsText: '',
    defaultValue: '',
    sensitive: false,
    optional: false,
  }))
  return [...rows, ...additions]
}
