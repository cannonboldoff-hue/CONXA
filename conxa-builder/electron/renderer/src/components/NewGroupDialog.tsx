import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { addGroupApp, createGroup } from '@/api/groupsApi'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ArrowLeft, ArrowRight, Loader2, Lock, Plus, Trash2 } from 'lucide-react'

type AppRow = { name: string; url: string }

// Sign-in pages for the quick-add chips. Plain public login URLs, nothing account-specific.
const QUICK_ADD: AppRow[] = [
  { name: 'Stripe', url: 'https://dashboard.stripe.com/login' },
  { name: 'Google', url: 'https://accounts.google.com' },
  { name: 'HubSpot', url: 'https://app.hubspot.com/login' },
  { name: 'Slack', url: 'https://slack.com/signin' },
]

const FIELD = 'h-10 rounded-[10px] border-[var(--border-default)] bg-[#2c2c2c] px-3 text-sm text-[#e8e6e3] placeholder:text-[#8e8e8e] dark:bg-[#2c2c2c]'

function withScheme(url: string): string {
  const v = url.trim()
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(v) ? v : `https://${v}`
}

/**
 * Two steps: name the group, then list the apps its workflows sign in to. "Create group & sign in"
 * lands on the group page with `state.autoConnect` set, which opens the first app's Connect straight
 * away; "Skip for now" creates the same group and leaves signing in for later.
 */
export function NewGroupDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [step, setStep] = useState<1 | 2>(1)
  const [name, setName] = useState('')
  const [apps, setApps] = useState<AppRow[]>([{ name: '', url: '' }])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const filled = apps.filter((a) => a.name.trim() || a.url.trim())
  const rowsComplete = filled.every((a) => a.name.trim() && a.url.trim())

  function reset() {
    setStep(1)
    setName('')
    setApps([{ name: '', url: '' }])
    setError('')
    setBusy(false)
  }

  function handleOpenChange(v: boolean) {
    if (busy) return
    if (!v) reset()
    onOpenChange(v)
  }

  function setRow(i: number, patch: Partial<AppRow>) {
    setApps((rows) => rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  }

  function quickAdd(q: AppRow) {
    if (apps.some((a) => a.name.trim().toLowerCase() === q.name.toLowerCase())) return
    // Fill the first blank row before adding a new one.
    const blank = apps.findIndex((a) => !a.name.trim() && !a.url.trim())
    setApps(blank === -1 ? [...apps, q] : apps.map((r, j) => (j === blank ? q : r)))
  }

  async function submit(connect: boolean) {
    if (!rowsComplete) {
      setError('Each app needs a name and a sign-in page, or remove the row.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const { group } = await createGroup(name.trim())
      const failed: string[] = []
      for (const a of filled) {
        try {
          await addGroupApp(group.id, a.name.trim(), withScheme(a.url), '')
        } catch {
          failed.push(a.name.trim())
        }
      }
      qc.invalidateQueries({ queryKey: ['groups'] })
      if (failed.length) toast.error(`Couldn’t add ${failed.join(', ')}. Add ${failed.length === 1 ? 'it' : 'them'} again from the group page.`)
      const added = filled.length - failed.length
      reset()
      onOpenChange(false)
      navigate(`/groups/${encodeURIComponent(group.id)}`, { state: { autoConnect: connect && added > 0 } })
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        showCloseButton={false}
        className={step === 1
          ? 'gap-5 rounded-[14px] bg-[#252525] p-7 ring-[var(--border-default)] sm:max-w-[540px]'
          : 'gap-5 rounded-[14px] bg-[#252525] p-7 ring-[var(--border-default)] sm:max-w-[620px]'}
      >
        <div className="space-y-2.5">
          <div className="flex gap-1.5" aria-hidden>
            <div className="h-[3px] flex-1 rounded-full bg-[#e8e6e3]" />
            <div className={step === 2 ? 'h-[3px] flex-1 rounded-full bg-[#e8e6e3]' : 'h-[3px] flex-1 rounded-full bg-white/[0.14]'} />
          </div>
          <div className="flex justify-between text-[0.6875rem] font-medium">
            {step === 1 ? (
              <>
                <span className="text-[#e8e6e3]">Step 1 of 2 · Name</span>
                <span className="text-[#8e8e8e]">Next · Sign-ins</span>
              </>
            ) : (
              <>
                <span className="text-[#8e8e8e]">Step 1 · Name — {name.trim()}</span>
                <span className="text-[#e8e6e3]">Step 2 of 2 · Sign-ins</span>
              </>
            )}
          </div>
        </div>

        {step === 1 ? (
          <>
            <div className="space-y-2">
              <DialogTitle className="text-[1.375rem] font-semibold leading-tight text-[#e8e6e3]">Name your group</DialogTitle>
              <DialogDescription className="text-sm leading-relaxed text-[#a3a3a3]">
                A group holds workflows that share the same sign-ins — like everything you do in your billing app.
              </DialogDescription>
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-group-name" className="text-xs font-medium text-[#a3a3a3]">Group name</Label>
              <Input
                id="new-group-name"
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && name.trim()) setStep(2) }}
                className={`${FIELD} h-[42px] text-sm`}
              />
              <p className="text-xs text-[#8e8e8e]">For example: Billing, Support, Onboarding</p>
            </div>
            <div className="flex justify-end gap-2.5 pt-1">
              <Button type="button" variant="outline" className="h-[38px] rounded-[10px] px-4" onClick={() => handleOpenChange(false)}>Cancel</Button>
              <Button type="button" variant="brand" className="h-[38px] rounded-[10px] px-4" disabled={!name.trim()} onClick={() => setStep(2)}>
                Next: sign-ins <ArrowRight className="size-4" />
              </Button>
            </div>
          </>
        ) : (
          <>
            <div className="space-y-2">
              <DialogTitle className="text-[1.375rem] font-semibold leading-tight text-[#e8e6e3]">Which apps does {name.trim()} sign in to?</DialogTitle>
              <DialogDescription className="text-sm leading-relaxed text-[#a3a3a3]">
                Add each app once. You’ll sign in to them next, and every workflow in this group reuses the same session.
              </DialogDescription>
            </div>

            <div className="space-y-2.5">
              <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_2.5rem] gap-2.5 text-xs font-medium text-[#a3a3a3]">
                <span>App name</span><span>Sign-in page</span><span />
              </div>
              {apps.map((a, i) => (
                <div key={i} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_2.5rem] items-center gap-2.5">
                  <Input value={a.name} onChange={(e) => setRow(i, { name: e.target.value })} placeholder="App name" aria-label={`App ${i + 1} name`} className={FIELD} />
                  <Input value={a.url} onChange={(e) => setRow(i, { url: e.target.value })} placeholder="https://app.example.com/login" aria-label={`App ${i + 1} sign-in page`} className={`${FIELD} font-mono text-xs`} />
                  {apps.length > 1 ? (
                    <Button type="button" variant="ghost" size="icon" className="size-10 text-[#a3a3a3]" aria-label={`Remove ${a.name.trim() || `app ${i + 1}`}`} onClick={() => setApps((rows) => rows.filter((_, j) => j !== i))}>
                      <Trash2 className="size-4" />
                    </Button>
                  ) : <span />}
                </div>
              ))}
              <Button type="button" variant="outline" className="h-8 rounded-[10px] border-dashed bg-transparent" onClick={() => setApps((rows) => [...rows, { name: '', url: '' }])}>
                <Plus className="size-4" /> Add another app
              </Button>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <span className="mr-0.5 text-xs text-[#8e8e8e]">Quick add</span>
              {QUICK_ADD.map((q) => (
                <button
                  key={q.name}
                  type="button"
                  onClick={() => quickAdd(q)}
                  className="inline-flex h-7 items-center gap-1 rounded-full border border-white/[0.14] px-2.5 text-xs font-medium text-[#e8e6e3] outline-none hover:bg-white/[0.04] focus-visible:ring-2 focus-visible:ring-brand/55"
                >
                  <Plus className="size-3" /> {q.name}
                </button>
              ))}
            </div>

            <div className="flex items-start gap-2.5 rounded-[10px] bg-[#2c2c2c] px-3.5 py-3">
              <Lock className="mt-px size-4 shrink-0 text-[#a3a3a3]" />
              <p className="text-xs leading-relaxed text-[#a3a3a3]">
                Passwords stay on this computer. Conxa keeps only the signed-in session, and it is never included in what you publish.
              </p>
            </div>

            {error && <p role="alert" className="text-sm text-status-error">✕ {error}</p>}

            <div className="flex items-center justify-between pt-1">
              <Button type="button" variant="ghost" className="h-[38px] rounded-[10px] px-3.5 text-[#a3a3a3]" disabled={busy} onClick={() => { setError(''); setStep(1) }}>
                <ArrowLeft className="size-4" /> Back
              </Button>
              <div className="flex gap-2.5">
                <Button type="button" variant="outline" className="h-[38px] rounded-[10px] px-4" disabled={busy} onClick={() => submit(false)}>Skip for now</Button>
                <Button type="button" variant="brand" className="h-[38px] rounded-[10px] px-4" disabled={busy || filled.length === 0} onClick={() => submit(true)}>
                  {busy ? <Loader2 className="size-4 animate-spin" /> : null}Create group &amp; sign in
                </Button>
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
