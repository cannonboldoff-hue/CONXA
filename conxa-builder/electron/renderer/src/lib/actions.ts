/** What each lifecycle action is called and how the strip runs it. Mirrors
 *  conversation/actions.py (python/conxa_compile/conversation) — a Python test keeps the ids in sync. */
export type ActionInfo = { label: string; kind: 'flow' | 'nav'; target?: 'edit' | 'publish'; strip?: boolean; primary?: boolean }

export const ACTION_INFO: Record<string, ActionInfo> = {
  sign_in: { label: 'Sign in', kind: 'flow', strip: true, primary: true },
  record: { label: 'Start recording', kind: 'flow', strip: true, primary: true },
  re_record: { label: 'Record again', kind: 'flow', strip: true },
  compile: { label: 'Compile', kind: 'flow', strip: true, primary: true },
  answer_clarify: { label: 'Answer questions', kind: 'flow', strip: true, primary: true },
  test: { label: 'Test it', kind: 'flow', strip: true, primary: true },
  autofix: { label: 'Fix it', kind: 'flow', strip: true },
  resume_autofix: { label: 'Keep fixing', kind: 'flow', strip: true },
  approve: { label: 'Approve', kind: 'flow', strip: true, primary: true },
  edit: { label: 'Open Human Edit', kind: 'nav', target: 'edit', strip: true },
  publish: { label: 'Publish', kind: 'nav', target: 'publish', strip: true },
  // Not on the strip: the active card carries these buttons itself.
  finish_sign_in: { label: 'I’m signed in', kind: 'flow' },
  cancel: { label: 'Cancel sign-in', kind: 'flow' },
  finish_recording: { label: 'Finish recording', kind: 'flow' },
  discard_recording: { label: 'Discard recording', kind: 'flow' },
  stop_test: { label: 'Stop the test', kind: 'flow' },
}
