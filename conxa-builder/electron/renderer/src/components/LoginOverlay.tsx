import { useState } from 'react'
import { Layers } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cmd, CmdError } from '@/lib/ipc'
import { errorMessage } from '@/api/workflowApi'
import type { Identity } from '@/contexts/AuthContext'

export function LoginOverlay({ onLogin }: { onLogin: (identity: Identity) => void }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSignIn() {
    setLoading(true)
    setError(null)
    try {
      const result = await cmd<{ identity: Identity }>('login')
      if (result?.identity) {
        onLogin(result.identity)
      } else {
        setError('Sign-in completed but no identity returned. Please try again.')
        setLoading(false)
      }
    } catch (err) {
      // A person's own Cancel is not an error: go back to the plain button.
      if (!(err instanceof CmdError && err.code === 'login_cancelled')) {
        setError(errorMessage(err, 'Sign-in didn\'t work. Click Sign in to try again.'))
      }
      setLoading(false)
    }
  }

  function handleCancel() {
    cmd('cancel_login').catch(() => {})
  }

  return (
    <div
      className="fixed inset-x-0 top-10 bottom-0 z-[9999] flex items-center justify-center bg-[#090b0d]/95 backdrop-blur-sm"
      aria-modal="true"
      role="dialog"
      aria-label="Sign in"
    >
      <div className="w-full max-w-sm rounded-2xl border border-white/10 bg-[#0d0f12] p-8 shadow-2xl">
        <div className="mb-8 flex flex-col items-center gap-4">
          <span className="flex h-14 w-14 items-center justify-center rounded-2xl border border-white/10 bg-white/5 shadow-md">
            <Layers className="size-7 text-white" strokeWidth={1.5} />
          </span>
          <div className="text-center">
            <h1 className="text-xl font-semibold text-white">Conxa Build Studio</h1>
            <p className="mt-1 text-sm text-zinc-400">Sign in to your workspace</p>
          </div>
        </div>

        <Button
          className="w-full"
          size="lg"
          onClick={handleSignIn}
          disabled={loading}
        >
          {loading ? 'Opening browser…' : 'Sign in'}
        </Button>

        {loading && (
          <p className="mt-4 text-center text-xs text-zinc-500">
            Complete sign-in in your browser window, then return here.
          </p>
        )}

        {loading && (
          <button
            type="button"
            className="mt-2 block w-full text-center text-xs text-zinc-400 underline hover:text-zinc-200"
            onClick={handleCancel}
          >
            Cancel
          </button>
        )}

        {error && (
          <p className="mt-4 text-center text-xs text-red-400">{error}</p>
        )}
      </div>
    </div>
  )
}
