import { useEffect } from 'react'
import { useNavigate, useMatch } from 'react-router-dom'
import { useShellData } from '@/hooks/useShellData'
import { useShellStore } from '@/store/shellStore'

/**
 * Shell keyboard map (Panels mockup): Ctrl K search, Ctrl N new workflow, Ctrl \ fold
 * navigation, Ctrl . fold inspector, Ctrl E open Human Edit for this workflow. Esc is left to
 * dialogs and sheets (Radix handles it) — it never stops a run.
 */
export function useShellShortcuts() {
  const navigate = useNavigate()
  const match = useMatch('/w/:workflowId')
  const { workflowsById } = useShellData()
  const { setSearchOpen, toggleNav, toggleInspector } = useShellStore.getState()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return
      const key = e.key.toLowerCase()
      if (key === 'k') {
        e.preventDefault()
        setSearchOpen(true)
      } else if (key === 'n') {
        e.preventDefault()
        navigate('/new')
      } else if (key === '\\') {
        e.preventDefault()
        toggleNav()
      } else if (key === '.') {
        e.preventDefault()
        toggleInspector()
      } else if (key === 'e' && match?.params.workflowId) {
        const w = workflowsById.get(match.params.workflowId)
        if (w?.workflow.skill_id) {
          e.preventDefault()
          navigate(`/edit/${w.workflow.skill_id}?from=${encodeURIComponent(`/w/${w.workflow.id}`)}`)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate, match?.params.workflowId, workflowsById, setSearchOpen, toggleNav, toggleInspector])
}
