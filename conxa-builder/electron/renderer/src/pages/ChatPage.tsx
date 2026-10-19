import { useEffect, useRef } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeftRight, CircleHelp, Download, Table2 } from 'lucide-react'
import { chatCreate, chatList } from '@/api/conversationApi'
import { PageHeader } from '@/components/layout/PageHeader'
import { Conversation } from '@/components/conversation/Conversation'
import { useThreadStore, selectThread } from '@/store/threadStore'

const NOTE = 'General chat · can read your groups and workflow names, can’t change anything'
const PLACEHOLDER_EXAMPLE =
  'e.g. Every Monday, export last week’s invoices from the billing portal and email the CSV to finance'

/** Ideas under a new chat's composer; clicking one fills the box with a ready-written request. */
const STARTERS = [
  {
    title: 'Fill a form from a spreadsheet',
    body: 'Enter each row of a sheet into a web form, one at a time.',
    text: 'For each row in my spreadsheet, fill in the new-customer form and submit it.',
    Icon: Table2,
  },
  {
    title: 'Export a recurring report',
    body: 'Download the same report on a schedule and send it on.',
    text: 'Every Monday, export last week’s report and email the file to my team.',
    Icon: Download,
  },
  {
    title: 'Keep two tools in sync',
    body: 'Copy a status or record from one app into another.',
    text: 'When a ticket is closed in the support desk, update the matching record in the CRM.',
    Icon: ArrowLeftRight,
  },
  {
    title: 'Ask how something works',
    body: 'Recording, review, publishing — get a plain answer.',
    text: 'How does recording a workflow work, and what happens after I record it?',
    Icon: CircleHelp,
  },
]

/**
 * General Chat. It plans a workflow before you record it: read-only over groups and workflow
 * names, and a drafted workflow is only created when the person confirms the draft card.
 * Replies use the workspace's AI credits.
 */
export function ChatPage() {
  const { chatId } = useParams<{ chatId?: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const location = useLocation()
  const send = useThreadStore((s) => s.send)
  const threadId = chatId ? `chat:${chatId}` : null
  const loaded = useThreadStore((s) => selectThread(threadId)(s).loaded)
  const chatsQ = useQuery({ queryKey: ['chats'], queryFn: chatList, retry: false })
  const title = chatsQ.data?.chats.find((c) => c.id === chatId)?.title ?? 'Chat'

  // A new chat is created on its first message, which is then sent once its thread is open.
  const pending = useRef<string | null>((location.state as { first?: string } | null)?.first ?? null)
  useEffect(() => {
    if (threadId && loaded && pending.current) {
      const text = pending.current
      pending.current = null
      void send(threadId, text).then(() => qc.invalidateQueries({ queryKey: ['chats'] }))
    }
  }, [threadId, loaded, send, qc])

  const startChat = async (text: string) => {
    const { chat } = await chatCreate()
    qc.invalidateQueries({ queryKey: ['chats'] })
    navigate(`/chat/${chat.id}`, { replace: true, state: { first: text } })
  }

  return (
    <>
      {chatId ? <PageHeader title={title} /> : null}
      <div className="h-full">
        <Conversation
          key={threadId ?? 'new'}
          threadId={threadId ?? 'chat:__new__'}
          virtual={!threadId}
          onSend={threadId ? undefined : startChat}
          centerWhenEmpty
          placeholder={threadId ? 'Plan a workflow, or ask how something works' : PLACEHOLDER_EXAMPLE}
          composerNote={<>{NOTE}. Replies use your workspace’s AI credits.</>}
          empty={
            <div className="flex flex-col items-center gap-3 text-center">
              <h2 className="text-[2rem] font-semibold leading-tight tracking-[-0.02em] text-[var(--text-primary)]">What are you about to automate?</h2>
              <p className="max-w-[32.5rem] text-[0.9375rem] text-[var(--text-secondary)]">
                Describe the task in plain words. We’ll shape it into a workflow together — nothing is created until you confirm.
              </p>
            </div>
          }
          emptyBelow={(fill) => (
            <section aria-labelledby="chat-starters" className="flex flex-col gap-3">
              <h3 id="chat-starters" className="text-xs font-medium tracking-[0.02em] text-[var(--text-meta)]">
                START FROM A COMMON PATTERN
              </h3>
              <div className="grid grid-cols-[repeat(auto-fit,minmax(18rem,1fr))] gap-2.5">
                {STARTERS.map(({ title, body, text, Icon }) => (
                  <button
                    key={title}
                    type="button"
                    onClick={() => fill(text)}
                    className="flex min-h-[76px] items-start gap-3 rounded-[12px] border border-[var(--border-subtle)] bg-[var(--bg-sidebar)] p-3.5 text-left transition-colors hover:border-[var(--border-default)] hover:bg-[var(--bg-raised)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/55"
                  >
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-[8px] border border-[var(--border-default)] bg-[var(--bg-surface)] text-[var(--text-secondary)]">
                      <Icon className="size-[15px]" aria-hidden />
                    </span>
                    <span className="flex flex-col gap-0.5">
                      <span className="text-[0.8125rem] font-medium text-[var(--text-primary)]">{title}</span>
                      <span className="text-xs leading-[1.45] text-[var(--text-meta)]">{body}</span>
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}
        />
      </div>
    </>
  )
}
