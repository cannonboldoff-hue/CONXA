import { useBlocker } from 'react-router-dom'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { useEditorStore } from '@/store/editorStore'

/**
 * Leaving Human Edit with an unsaved change asks first (States mockup): "Save and leave" saves the open
 * step and goes on, "Discard" drops the edit, and Esc / "Keep editing" stays put. Everything already
 * saved is untouched either way. `save` resolves true when the open step saved cleanly.
 */
export function UnsavedChangeGuard({ save }: { save: () => Promise<boolean> }) {
  const dirty = useEditorStore((s) => s.dirtySteps)
  const clearAllDirty = useEditorStore((s) => s.clearAllDirty)
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      useEditorStore.getState().dirtySteps.size > 0 && currentLocation.pathname !== nextLocation.pathname,
  )
  const steps = [...dirty].sort((a, b) => a - b)
  const what = steps.length === 1 ? `step ${steps[0]}` : `${steps.length} steps`

  return (
    <AlertDialog open={blocker.state === 'blocked'} onOpenChange={(o) => { if (!o && blocker.state === 'blocked') blocker.reset() }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{`Save the change to ${what}?`}</AlertDialogTitle>
          <AlertDialogDescription>
            You edited {what} but haven’t saved it. Everything else is already saved.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep editing</AlertDialogCancel>
          <AlertDialogAction
            variant="outline"
            onClick={() => {
              clearAllDirty()
              if (blocker.state === 'blocked') blocker.proceed()
            }}
          >
            Discard
          </AlertDialogAction>
          <AlertDialogAction
            onClick={(e) => {
              e.preventDefault()
              void save().then((ok) => {
                if (ok && blocker.state === 'blocked') blocker.proceed()
                else if (blocker.state === 'blocked') blocker.reset()
              })
            }}
          >
            Save and leave
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
