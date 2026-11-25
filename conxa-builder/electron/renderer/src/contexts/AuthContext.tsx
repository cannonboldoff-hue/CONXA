import { createContext, useContext } from 'react'
import { cmd, CmdError } from '@/lib/ipc'

export type Identity = {
  email: string | null
  name?: string
  user_id?: string
  org_id?: string
  org_name?: string
}

type AuthContextValue = {
  identity: Identity | null
  setIdentity: (identity: Identity | null) => void
  logout: () => Promise<string | null>
}

export const AuthContext = createContext<AuthContextValue>({
  identity: null,
  setIdentity: () => {},
  logout: async () => null,
})

export function useAuth() {
  return useContext(AuthContext)
}

/** Signs out. Returns a plain sentence when it can't (a recording is running), otherwise null. */
export async function performLogout(setIdentity: (i: Identity | null) => void): Promise<string | null> {
  try {
    await cmd('logout')
  } catch (err) {
    // Switching accounts restarts the engine, which would cut a running recording off.
    if (err instanceof CmdError && err.code === 'work_running') {
      return 'Finish or stop the recording first, then sign out.'
    }
    // proceed regardless
  }
  setIdentity(null)
  return null
}
