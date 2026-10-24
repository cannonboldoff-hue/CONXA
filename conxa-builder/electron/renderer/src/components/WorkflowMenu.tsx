import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { deleteWorkflowEntity, updateWorkflow } from '@/api/workflowsApi'
import { MenuItem } from '@/components/GroupAuthWizard'
import { Button } from '@/components/ui/button'
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
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import { MoreHorizontal, Pencil, Trash2 } from 'lucide-react'

/** The sidebar's "…" menu on a workflow row: Rename and Delete, each confirmed in its own dialog. */
export function WorkflowMenu({ workflowId, workflowName, className }: { workflowId: string; workflowName: string; className?: string }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [deleting, setDeleting] = useState(false)

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['workflows'] })
    qc.invalidateQueries({ queryKey: ['groups'] })
    qc.invalidateQueries({ queryKey: ['group'] })
  }

  const renameMut = useMutation({
    mutationFn: () => updateWorkflow(workflowId, { name: renameValue.trim() }),
    onSuccess: () => {
      setRenaming(false)
      refresh()
    },
    onError: (e) => toast.error((e as Error).message),
  })

  const deleteMut = useMutation({
    mutationFn: () => deleteWorkflowEntity(workflowId),
    onSuccess: () => {
      setDeleting(false)
      refresh()
      if (pathname === `/w/${workflowId}`) navigate('/workflows')
    },
    onError: (e) => toast.error((e as Error).message || 'Failed to delete workflow.'),
  })

  const canSave = !!renameValue.trim() && renameValue.trim() !== workflowName && !renameMut.isPending

  return (
    <>
      <Popover open={menuOpen} onOpenChange={setMenuOpen}>
        <PopoverTrigger asChild>
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label={`Options for ${workflowName}`}
            className={cn('text-[#8e8e8e] hover:text-[#e8e6e3] aria-expanded:opacity-100', className)}
          >
            <MoreHorizontal className="size-3.5" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-44 p-1">
          <MenuItem icon={<Pencil />} onSelect={() => { setMenuOpen(false); setRenameValue(workflowName); setRenaming(true) }}>
            Rename workflow
          </MenuItem>
          <MenuItem icon={<Trash2 />} danger onSelect={() => { setMenuOpen(false); setDeleting(true) }}>
            Delete workflow
          </MenuItem>
        </PopoverContent>
      </Popover>

      <Dialog open={renaming} onOpenChange={setRenaming}>
        <DialogContent className="border-white/10 bg-[#0d0f12] text-zinc-100">
          <DialogHeader><DialogTitle className="text-white">Rename workflow</DialogTitle></DialogHeader>
          <form
            className="space-y-4 pt-2"
            onSubmit={(e) => { e.preventDefault(); if (canSave) renameMut.mutate() }}
          >
            <Input autoFocus value={renameValue} onChange={(e) => setRenameValue(e.target.value)} className="border-white/10 bg-white/5 text-zinc-100" />
            <Button type="submit" className="w-full" disabled={!canSave}>Save</Button>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleting} onOpenChange={setDeleting}>
        <AlertDialogContent className="border-white/10 bg-[#0d0f12] text-zinc-100">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-white">Delete &ldquo;{workflowName}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription className="text-zinc-400">
              This deletes the workflow, its recording, and any built output. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-white/10 bg-white/5 text-zinc-200">Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-600 text-white hover:bg-red-700"
              disabled={deleteMut.isPending}
              onClick={(e) => { e.preventDefault(); deleteMut.mutate() }}
            >
              {deleteMut.isPending ? 'Deleting...' : 'Delete'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
