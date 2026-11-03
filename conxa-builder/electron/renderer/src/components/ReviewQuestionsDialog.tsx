import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  acceptForEachSuggestion,
  confirmOptionalInterstitial,
  errorMessage,
  rejectForEachSuggestion,
} from '@/api/workflowApi'
import { CmdError } from '@/lib/ipc'
import type { ForEachSuggestion, StepEditorDTO, WorkflowResponse } from '@/types/workflow'

// ── Generic review-question chain ──────────────────────────────────────────────────────────
//
// One pending decision (a suggestion, a flagged step, …) can need more than one dependent
// Yes/No answer before it's actually applied — e.g. "turn this into a loop?" and, only if
// that was Yes, a separate "remove the leftover click too?" — while still landing as ONE
// atomic backend write. Rather than hard-code that two-question shape into the dialog (which
// would leave the NEXT compound suggestion re-inventing the same bookkeeping), a `ReviewItem`
// is anything that can be asked as a chain of Yes/No questions: `nextQuestion` looks at every
// answer collected so far and returns the next question to ask, or `null` once nothing more
// is needed; `commit` then performs the one real mutation. The dialog below only ever drives
// this contract — it has no idea what a "loop" or a "redundant click" is.
export type ReviewQuestion = { title: string; description: string }

export type ReviewItem = {
  /** Stable across renders for one pending decision (e.g. `loop:${suggestion.id}`) — used to
   *  detect "this is a new item" and reset the in-progress answer chain. */
  key: string
  /** `answers` is every Yes/No given so far for THIS item, oldest first. Called with `[]` for
   *  the first question. Must be pure — no side effects, no network calls. */
  nextQuestion: (answers: boolean[]) => ReviewQuestion | null
  /** Called once `nextQuestion(answers)` returns null. Performs the real mutation and returns
   *  the fresh workflow; the caller feeds it to `onWorkflowUpdated`. Any toast/success message
   *  is this function's own responsibility — the driver stays domain-agnostic. */
  commit: (answers: boolean[]) => Promise<WorkflowResponse>
}

function useReviewQuestionChain(items: ReviewItem[], onWorkflowUpdated: (wf: WorkflowResponse) => void) {
  const [busy, setBusy] = useState(false)
  const [answers, setAnswers] = useState<boolean[]>([])
  const currentKeyRef = useRef<string | null>(null)

  const current = items[0]

  // A new pending item (a fresh suggestion/step, or the previous one just got committed and
  // resolved) always starts its own chain from scratch.
  useEffect(() => {
    if (current?.key !== currentKeyRef.current) {
      currentKeyRef.current = current?.key ?? null
      setAnswers([])
    }
  }, [current?.key])

  const question = current?.nextQuestion(answers) ?? null

  const answer = async (yes: boolean) => {
    if (!current) return
    const nextAnswers = [...answers, yes]
    if (current.nextQuestion(nextAnswers)) {
      setAnswers(nextAnswers) // more questions left in this same item's chain
      return
    }
    setBusy(true)
    try {
      const updated = await current.commit(nextAnswers)
      onWorkflowUpdated(updated)
      setAnswers([])
    } catch (err) {
      toast.error(errorMessage(err, 'Could not save this answer — try again'))
    } finally {
      setBusy(false)
    }
  }

  return { current, question, answer, busy }
}

// ── Concrete review items ──────────────────────────────────────────────────────────────────

/** A compiler-detected "generalize this to a loop" suggestion. Up to two dependent
 * questions — accepting the loop, then (only if accepted, and only when this suggestion has
 * a redundant click) whether to also remove that leftover click — committed as the ONE
 * atomic `apply_for_each_loop_suggestion` write either way (`acceptForEachSuggestion`'s
 * `removeRedundantClick` param). Rejecting the loop ends the chain immediately; the click
 * question never applies to a rejected loop. */
function buildLoopSuggestionReviewItem(skillId: string, suggestion: ForEachSuggestion, wf: WorkflowResponse): ReviewItem {
  return {
    key: `loop:${suggestion.id}`,
    nextQuestion(answers) {
      if (answers.length === 0) {
        return { title: 'Turn this into a loop?', description: suggestion.why }
      }
      const loopAccepted = answers[0]
      if (!loopAccepted) return null
      if (suggestion.redundant_click_key && answers.length === 1) {
        return {
          title: 'Remove the leftover click?',
          description: suggestion.redundant_click_why ?? 'Remove the leftover click on this one file?',
        }
      }
      return null
    },
    async commit(answers) {
      const loopAccepted = answers[0]
      if (!loopAccepted) {
        await rejectForEachSuggestion(skillId, suggestion)
        // reject_for_each_suggestion only returns {ok} — the compiler already excludes a
        // previously-rejected suggestion on the next compile, so just drop it from the
        // in-memory workflow rather than round-tripping a refetch for one filtered field.
        return {
          ...wf,
          compile_health: {
            ...wf.compile_health,
            for_each_suggestions: (wf.compile_health.for_each_suggestions ?? []).filter((s) => s.id !== suggestion.id),
          },
        }
      }
      const removeRedundantClick = suggestion.redundant_click_key ? Boolean(answers[1]) : true
      let result
      try {
        result = await acceptForEachSuggestion(skillId, suggestion, removeRedundantClick)
      } catch (err) {
        if (!(err instanceof CmdError) || err.code !== 'suggestion_stale') throw err
        // The steps changed since this was suggested, so it can never apply. This dialog is
        // unskippable: leaving it pending would lock the editor, so drop it and move on.
        await rejectForEachSuggestion(skillId, suggestion).catch(() => undefined)
        toast.info('This loop suggestion is out of date (the steps changed), so it was dismissed')
        return {
          ...wf,
          compile_health: {
            ...wf.compile_health,
            for_each_suggestions: (wf.compile_health.for_each_suggestions ?? []).filter((s) => s.id !== suggestion.id),
          },
        }
      }
      toast.success(
        suggestion.redundant_click_key
          ? removeRedundantClick
            ? 'Generalized to a loop — leftover click removed too'
            : 'Generalized to a loop — leftover click kept in place'
          : 'Generalized to a loop',
      )
      return result.workflow
    },
  }
}

/** A recorder-flagged optional interstitial (cookie banner / dialog). One question, no
 * dependent follow-up. */
function buildOptionalPopupReviewItem(skillId: string, step: StepEditorDTO): ReviewItem {
  return {
    key: `optional:${step.step_index}`,
    nextQuestion(answers) {
      if (answers.length > 0) return null
      return {
        title: 'Optional popup?',
        description:
          "This step may be an optional popup that doesn't always appear. Should it be treated as optional (skipped if not present)?",
      }
    },
    async commit(answers) {
      const treatAsOptional = answers[0]
      const result = await confirmOptionalInterstitial(skillId, step.step_index, !treatAsOptional)
      toast.success(treatAsOptional ? 'Converted to an optional try-dismiss branch' : 'Kept as a normal step')
      return result.workflow
    },
  }
}

/** Each source turns the live workflow into zero or more pending `ReviewItem`s. A new question
 * kind = one new builder + one entry here; the dialog and chain driver never change. */
const REVIEW_SOURCES: ((skillId: string, wf: WorkflowResponse) => ReviewItem[])[] = [
  (skillId, wf) => (wf.compile_health.for_each_suggestions ?? []).map((s) => buildLoopSuggestionReviewItem(skillId, s, wf)),
  (skillId, wf) => wf.steps.filter((s) => s.optional_hint).map((step) => buildOptionalPopupReviewItem(skillId, step)),
]

// ── The dialog itself ──────────────────────────────────────────────────────────────────────

type Props = {
  skillId: string
  wf: WorkflowResponse
  onWorkflowUpdated: (wf: WorkflowResponse) => void
}

/**
 * Mandatory Yes/No review questions shown the moment Human Edit opens, one at a time,
 * unskippable (no close button, Esc/outside-click do nothing) — replaces the old
 * always-dismissible ForEachSuggestionBanner and the inline optional-popup chip as the
 * *first* thing a reviewer sees. Recomputes its pending items from the live workflow after
 * every commit, so it closes itself the moment nothing is left; a recompile regenerates
 * fresh hints/suggestions, which is what "answered once per compile" falls out of.
 *
 * Purely a driver over `ReviewItem`s (see above) — adding a future compound decision (one
 * mutation gated behind several dependent Yes/No answers) never touches this component.
 */
export function ReviewQuestionsDialog({ skillId, wf, onWorkflowUpdated }: Props) {
  const items = useMemo(() => REVIEW_SOURCES.flatMap((source) => source(skillId, wf)), [skillId, wf])

  const { current, question, answer, busy } = useReviewQuestionChain(items, onWorkflowUpdated)

  if (!current || !question) return null

  return (
    <Dialog open>
      <DialogContent
        showCloseButton={false}
        onEscapeKeyDown={(e) => e.preventDefault()}
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>{question.title}</DialogTitle>
          <DialogDescription>{question.description}</DialogDescription>
        </DialogHeader>
        {items.length > 1 && (
          <p className="text-xs text-muted-foreground">{items.length} questions to answer before continuing.</p>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" disabled={busy} onClick={() => void answer(false)}>
            No
          </Button>
          <Button type="button" variant="brand" disabled={busy} onClick={() => void answer(true)}>
            Yes
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
