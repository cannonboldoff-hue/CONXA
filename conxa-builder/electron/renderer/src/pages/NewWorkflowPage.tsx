import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchGroup } from '@/api/groupsApi'
import { createWorkflow } from '@/api/workflowsApi'
import { NewGroupDialog } from '@/components/NewGroupDialog'
import { StatusGlyph } from '@/components/shell/StatusGlyph'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useShellData } from '@/hooks/useShellData'
import { useShellStore } from '@/store/shellStore'
import { useBrowserStore } from '@/store/browserStore'
import { cn } from '@/lib/utils'

const URL_RE = /^https?:\/\/[^\s/]+\.[^\s/]+/

function hostOf(url: string): string {
  try {
    return new URL(url.trim()).hostname
  } catch {
    return ''
  }
}

/** A workflow needs a group to live in, so with none yet the group dialog opens first. */
function GroupRequired() {
  const [open, setOpen] = useState(true)
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="flex max-w-sm flex-col items-center gap-4 text-center">
        <div>
          <p className="text-sm font-medium text-[var(--text-primary)]">Create a group first</p>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            Every workflow lives in a group, which holds the sign-ins its workflows share. Create one, then record your first workflow in it.
          </p>
        </div>
        <Button type="button" variant="brand" onClick={() => setOpen(true)}>Create a group</Button>
        <NewGroupDialog open={open} onOpenChange={setOpen} />
      </div>
    </div>
  )
}

/**
 * New workflow: name, group, starting page, a required sentence about the task. A workflow
 * belongs to exactly one existing group from the moment it exists — with no groups yet this page
 * sends the user to create one first. "Create and start recording" creates the workflow and opens
 * its conversation, which signs in first if the app has no saved sign-in.
 */
export function NewWorkflowPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { groups, loading, failed } = useShellData()
  const draft = useShellStore((s) => s.newWorkflowDraft)
  const setDraft = useShellStore((s) => s.setNewWorkflowDraft)

  const [name, setName] = useState(draft?.name ?? '')
  const [url, setUrl] = useState(draft?.starting_page ?? '')
  const [task, setTask] = useState(draft?.task ?? '')
  const [groupId, setGroupId] = useState('')
  const [tried, setTried] = useState(false)

  useEffect(() => {
    if (groupId) return
    const byDraft = draft?.group ? groups.find((g) => g.group.name === draft.group) : undefined
    if (byDraft) setGroupId(byDraft.group.id)
    else if (groups[0]) setGroupId(groups[0].group.id)
  }, [groups, groupId, draft])

  const groupName = groups.find((g) => g.group.id === groupId)?.group.name ?? ''
  const groupQ = useQuery({ queryKey: ['group', groupId], queryFn: () => fetchGroup(groupId), enabled: Boolean(groupId) })

  const nameOk = name.trim().length > 0
  const urlOk = URL_RE.test(url.trim())
  const groupOk = Boolean(groupId)
  const taskOk = task.trim().length > 0

  // Does an app in the chosen group already cover this site, and is it signed in?
  const match = useMemo(() => {
    const host = hostOf(url)
    if (!urlOk || !host) return null
    return groupQ.data?.group.apps.find((a) => [a.login_url, a.success_url].some((u) => hostOf(u) === host)) ?? null
  }, [url, urlOk, groupQ.data])

  // Studio's own browser panel is open for recording (Electron could open its debugging port).
  const inPanel = useBrowserStore((s) => s.available)
  const needsSignIn = Boolean(urlOk && groupQ.data && !match?.captured_at)

  const create = useMutation({
    mutationFn: () => createWorkflow({ name: name.trim(), target_url: url.trim(), group_id: groupId, task_description: task.trim() }),
    onSuccess: ({ workflow }) => {
      qc.invalidateQueries({ queryKey: ['workflows'] })
      qc.invalidateQueries({ queryKey: ['groups'] })
      setDraft(null)
      navigate(`/w/${workflow.id}`, { state: { startRecording: true } })
    },
  })

  const submit = () => (nameOk && urlOk && groupOk && taskOk ?create.mutate() : setTried(true))
  const err = 'mt-1 text-xs text-status-error'
  const radio = (on: boolean) =>
    cn('flex cursor-pointer items-center gap-2.5 rounded-[10px] border px-3 py-2 text-sm', on ? 'border-white/25 bg-white/[0.07]' : 'border-[var(--border-default)] hover:bg-white/[0.04]')

  // Only when both lists answered — a failed read means "unknown", not "no groups".
  if (!loading && !failed && groups.length === 0) return <GroupRequired />

  return (
    <>
      <div className="h-full overflow-y-auto">
        <form className="mx-auto max-w-[36rem] space-y-6 px-4 py-8" onSubmit={(e) => { e.preventDefault(); submit() }}>
          <p className="text-[0.9375rem] leading-[1.65] text-[var(--text-secondary)]">
            Tell Conxa what you’re about to teach it. You’ll record the task right after this — no technical setup.
          </p>

          <div>
            <Label htmlFor="nw-name">Name</Label>
            <Input id="nw-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Create invoice" aria-invalid={tried && !nameOk} disabled={create.isPending} className="mt-1.5" />
            {tried && !nameOk && <p className={err}>✕ Give it a name people will recognise in the list.</p>}
          </div>

          <fieldset>
            <legend className="mb-1.5 text-sm font-medium">Group</legend>
            <div className="space-y-1.5">
              {groups.map((g) => (
                <label key={g.group.id} className={radio(groupId === g.group.id)}>
                  <input type="radio" name="group" className="accent-white" checked={groupId === g.group.id} onChange={() => setGroupId(g.group.id)} />
                  {g.group.name}
                </label>
              ))}
            </div>
            {tried && !groupOk && <p className={err}>✕ Choose a group.</p>}
          </fieldset>

          <div>
            <Label htmlFor="nw-url">Starting page</Label>
            <Input id="nw-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://billing.example.com/invoices/new" aria-invalid={tried && !urlOk} disabled={create.isPending} className="mt-1.5 font-mono text-sm" />
            {tried && !urlOk && <p className={err}>✕ Enter the full address of the page where the task starts, like https://billing.example.com/invoices/new</p>}
            {urlOk && groupQ.data && (
              <p className="mt-1.5 flex items-start gap-1.5 text-xs text-[var(--text-secondary)]">
                {match?.captured_at ? (
                  <><StatusGlyph kind="ok" /> Uses {match.name} — already signed in for {groupName}</>
                ) : (
                  <><StatusGlyph kind="warn" /> No sign-in saved for this site in {groupName}. The browser opens on its sign-in page first — sign in there, and recording starts after.</>
                )}
              </p>
            )}
          </div>

          <div>
            <Label htmlFor="nw-task">What does the task do?</Label>
            <p className="text-xs text-[var(--text-meta)]">Helps Conxa understand the workflow better and ask better questions afterwards.</p>
            <Textarea id="nw-task" value={task} onChange={(e) => setTask(e.target.value)} rows={3} aria-invalid={tried && !taskOk} disabled={create.isPending} className="mt-1.5" />
            {tried && !taskOk && <p className={err}>✕ Describe what the task does in a sentence.</p>}
          </div>

          {inPanel && (
            <section aria-label="What happens next" className="rounded-[10px] border border-[var(--border-subtle)] bg-[var(--bg-raised)] p-4">
              <h3 className="text-xs font-medium text-[var(--text-meta)]">What happens next</h3>
              <ol className="mt-2 space-y-1.5 text-sm text-[var(--text-secondary)]">
                {needsSignIn && <li><span className="mr-2 tabular-nums text-[var(--text-meta)]">·</span>A browser opens on the right, inside Conxa, on the sign-in page. Sign in — nothing is recorded yet.</li>}
                <li><span className="mr-2 tabular-nums text-[var(--text-meta)]">·</span>{needsSignIn ? 'Then recording starts on your starting page.' : 'A browser opens on the right, inside Conxa, on your starting page, and recording starts.'}</li>
                <li><span className="mr-2 tabular-nums text-[var(--text-meta)]">·</span>Do the task as you normally would. Each action shows up in the conversation as it’s captured.</li>
                <li><span className="mr-2 tabular-nums text-[var(--text-meta)]">·</span>Press Finish and save when you’re done. You can pause or widen the browser at any time.</li>
              </ol>
            </section>
          )}

          {create.isError && <p className="text-sm text-status-error">✕ {(create.error as Error).message}</p>}

          <div className="flex items-center gap-2">
            <Button type="submit" variant="brand" disabled={create.isPending}>{create.isPending ? 'Creating…' : 'Create and start recording'}</Button>
            <Button type="button" variant="ghost" disabled={create.isPending} onClick={() => { setDraft(null); navigate(-1) }}>Cancel</Button>
          </div>
        </form>
      </div>
    </>
  )
}
