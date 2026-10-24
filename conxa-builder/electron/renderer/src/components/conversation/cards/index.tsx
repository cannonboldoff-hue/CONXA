import type { ComponentType } from 'react'
import { RecordingCard, SignInCard } from '@/components/conversation/cards/FlowCards'
import { ClarifyQuestionCard, CompileCard, ForkVariantCard, RecordPlanCard, WorkflowSummaryCard } from '@/components/conversation/cards/CompileClarifyCards'
import { AutofixCard, TestInputsCard, TestRunCard } from '@/components/conversation/cards/TestCards'
import { ActionLinkCard, ConfirmActionCard } from '@/components/conversation/cards/ActionCards'
import {
  DraftWorkflowCard,
  ErrorCard,
  PendingChangeCard,
  UnknownCard,
  type CardProps,
} from '@/components/conversation/cards/ChangeAndDraftCards'

/** Card types the conversation can render. Later phases register their cards here
 *  (recording, compile progress, Clarify questions, test run, failure, passed). */
export const CARD_REGISTRY: Record<string, ComponentType<CardProps>> = {
  pending_change: PendingChangeCard,
  draft_workflow: DraftWorkflowCard,
  error: ErrorCard,
  sign_in: SignInCard,
  recording: RecordingCard,
  record_plan: RecordPlanCard,
  compile: CompileCard,
  clarify_question: ClarifyQuestionCard,
  fork_variant: ForkVariantCard,
  workflow_summary: WorkflowSummaryCard,
  test_inputs: TestInputsCard,
  test_run: TestRunCard,
  autofix: AutofixCard,
  confirm_action: ConfirmActionCard,
  action_link: ActionLinkCard,
}

export function cardComponent(cardType: string | undefined): ComponentType<CardProps> {
  return (cardType && CARD_REGISTRY[cardType]) || UnknownCard
}
