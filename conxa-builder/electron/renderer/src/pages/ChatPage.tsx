import { useEffect, useRef } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { chatCreate, chatList } from '@/api/conversationApi'
import { PageHeader } from '@/components/layout/PageHeader'
import { Conversation } from '@/components/conversation/Conversation'
import { useThreadStore, selectThread } from '@/store/threadStore'

const NOTE = 'General chat · can read your groups and workflow names, can’t change anything'

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
      <PageHeader title={chatId ? title : 'New chat'} />
      <div className="h-full">
        <Conversation
          key={threadId ?? 'new'}
          threadId={threadId ?? 'chat:__new__'}
          virtual={!threadId}
          onSend={threadId ? undefined : startChat}
          placeholder="Plan a workflow, or ask how something works"
          composerNote={<>{NOTE}. Replies use your workspace’s AI credits.</>}
          empty={
            <div className="space-y-2 pt-8 text-center">
              <h2 className="text-2xl font-semibold text-[var(--text-primary)]">What are you about to automate?</h2>
              <p className="text-sm text-[var(--text-secondary)]">
                Describe the task and I’ll help you shape it into a workflow — nothing is created until you confirm.
              </p>
            </div>
          }
        />
      </div>
    </>
  )
}
