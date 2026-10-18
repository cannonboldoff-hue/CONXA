import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createGroup, fetchGroup } from '@/api/groupsApi'
import { createWorkflow } from '@/api/workflowsApi'
import { PageHeader } from '@/components/layout/PageHeader'
import { StatusGlyph } from '@/components/shell/StatusGlyph'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useShellData } from '@/hooks/useShellData'
import { useShellStore } from '@/store/shellStore'
import { cn } from '@/lib/utils'

const NEW_GROUP = '__new__'
const URL_RE = /^https?:\/\/[^\s/]+\.[^\s/]+/

function hostOf(url: string): string {
  try {
    return new URL(url.trim()).hostname
  } catch {
    return ''
  }
}

/**
 * New workflow: name, group, starting page, an optional sentence about the task. A workflow
 * belongs to exactly one group from the moment it exists. "Create and start recording" creates
 * it and opens its conversation, which signs in first if the app has no saved sign-in.
 */
export function NewWorkflowPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { groups } = useShellData()
  const draft = useShellStore((s) => s.newWorkflowDraft)
  const setDraft = useShellStore((s) => s.setNewWorkflowDraft)

  const [name, setName] = useState(draft?.name ?? '')
  const [url, setUrl] = useState(draft?.starting_page ?? '')
  const [task, setTask] = useState(draft?.task ?? '')
  const [groupId, setGroupId] = useState('')
  const [newGroupName, setNewGroupName] = useState('')
  const [tried, setTried] = useState(false)

  useEffect(() => {
    if (groupId) return
    const byDraft = draft?.group ? groups.find((g) => g.group.name === draft.group) : undefined
    if (byDraft) setGroupId(byDraft.group.id)
    else if (groups[0]) setGroupId(groups[0].group.id)
  }, [groups, groupId, draft])

  const isNewGroup = groupId === NEW_GROUP || (groups.length === 0 && !groupId)
  const groupName = isNewGroup ? newGroupName.trim() || 'the new group' : groups.find((g) => g.group.id === groupId)?.group.name ?? ''
  const groupQ = useQuery({ queryKey: ['group', groupId], queryFn: () => fetchGroup(groupId), enabled: Boolean(groupId) && !isNewGroup })

  const nameOk = name.trim().length > 0
  const urlOk = URL_RE.test(url.trim())
  const groupOk = isNewGroup ? newGroupName.trim().length > 0 : Boolean(groupId)

  // Does an app in the chosen group already cover this site, and is it signed in?
  const match = useMemo(() => {
    const host = hostOf(url)
    if (!urlOk || !host || isNewGroup) return null
    return groupQ.data?.group.apps.find((a) => [a.login_url, a.success_url].some((u) => hostOf(u) === host)) ?? null
  }, [url, urlOk, isNewGroup, groupQ.data])

  const create = useMutation({
    mutationFn: async () => {
      const gid = isNewGroup ? (await createGroup(newGroupName.trim())).group.id : groupId
      return createWorkflow({ name: name.trim(), target_url: url.trim(), group_id: gid, task_description: task.trim() })
    },
    onSuccess: ({ workflow }) => {
      qc.invalidateQueries({ queryKey: ['workflows'] })
      qc.invalidateQueries({ queryKey: ['groups'] })
      setDraft(null)
      navigate(`/w/${workflow.id}`, { state: { startRecording: true } })
    },
  })

  const submit = () => (nameOk && urlOk && groupOk ? create.mutate() : setTried(true))
  const err = 'mt-1 text-xs text-status-error'
  const radio = (on: boolean) =>
    cn('flex cursor-pointer items-center gap-2.5 rounded-[10px] border px-3 py-2 text-sm', on ? 'border-white/25 bg-white/[0.07]' : 'border-[var(--border-default)] hover:bg-white/[0.04]')

  return (
    <>
      <PageHeader title="New workflow" />
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
              <label className={radio(isNewGroup)}>
                <input type="radio" name="group" className="accent-white" checked={isNewGroup} onChange={() => setGroupId(NEW_GROUP)} />
                New group
              </label>
            </div>
            {isNewGroup && (
              <div className="mt-2">
                <Input value={newGroupName} onChange={(e) => setNewGroupName(e.target.value)} placeholder="Group name" aria-label="New group name" aria-invalid={tried && !groupOk} />
                {tried && !groupOk && <p className={err}>✕ Name the new group.</p>}
                <p className="mt-1 text-xs text-[var(--text-meta)]">A new group starts with no sign-ins. You’ll add the app it uses on the next screen.</p>
              </div>
            )}
          </fieldset>

          <div>
            <Label htmlFor="nw-url">Starting page</Label>
            <Input id="nw-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://billing.example.com/invoices/new" aria-invalid={tried && !urlOk} disabled={create.isPending} className="mt-1.5 font-mono text-sm" />
            {tried && !urlOk && <p className={err}>✕ Enter the full address of the page where the task starts, like https://billing.example.com/invoices/new</p>}
            {urlOk && !isNewGroup && groupQ.data && (
              <p className="mt-1.5 flex items-start gap-1.5 text-xs text-[var(--text-secondary)]">
                {match?.captured_at ? (
                  <><StatusGlyph kind="ok" /> Uses {match.name} — already signed in for {groupName}</>
                ) : (
                  <><StatusGlyph kind="warn" /> No sign-in saved for this site in {groupName}. The browser opens on its sign-in page first.</>
                )}
              </p>
            )}
          </div>

          <div>
            <Label htmlFor="nw-task">What does the task do?</Label>
            <p className="text-xs text-[var(--text-meta)]">Optional — helps Conxa ask better questions afterwards</p>
            <Textarea id="nw-task" value={task} onChange={(e) => setTask(e.target.value)} rows={3} disabled={create.isPending} className="mt-1.5" />
          </div>

          {create.isError && <p className="text-sm text-status-error">✕ {(create.error as Error).message}</p>}

          <div className="flex items-center gap-2">
            <Button type="submit" variant="brand" disabled={create.isPending}>{create.isPending ? 'Creating…' : 'Create and start recording'}</Button>
            <Button type="button" variant="ghost" disabled={create.isPending} onClick={() => { setDraft(null); navigate(-1) }}>Cancel</Button>
            <span className="ml-auto text-xs text-[var(--text-meta)]">Opens the recording browser in its own window</span>
          </div>
        </form>
      </div>
    </>
  )
}
