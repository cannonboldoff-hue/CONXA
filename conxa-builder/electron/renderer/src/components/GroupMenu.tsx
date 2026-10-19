import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { deleteGroup, renameGroup } from '@/api/groupsApi'
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

/** The sidebar's "…" menu on a group row: Rename and Delete, each confirmed in its own dialog. */
export function GroupMenu({ groupId, groupName, className }: { groupId: string; groupName: string; className?: string }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [deleting, setDeleting] = useState(false)

  const renameMut = useMutation({
    mutationFn: () => renameGroup(groupId, renameValue),
    onSuccess: () => {
      setRenaming(false)
      qc.invalidateQueries({ queryKey: ['group', groupId] })
      qc.invalidateQueries({ queryKey: ['groups'] })
    },
  })

  const deleteMut = useMutation({
    mutationFn: () => deleteGroup(groupId),
    onSuccess: () => {
      setDeleting(false)
      qc.invalidateQueries({ queryKey: ['groups'] })
      if (pathname === `/groups/${groupId}`) navigate('/workflows')
    },
  })

  return (
    <>
      <Popover open={menuOpen} onOpenChange={setMenuOpen}>
        <PopoverTrigger asChild>
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label={`Options for ${groupName}`}
            className={cn('text-[#8e8e8e] hover:text-[#e8e6e3] aria-expanded:opacity-100', className)}
          >
            <MoreHorizontal className="size-3.5" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-44 p-1">
          <MenuItem icon={<Pencil />} onSelect={() => { setMenuOpen(false); setRenameValue(groupName); setRenaming(true) }}>
            Rename group
          </MenuItem>
          <MenuItem icon={<Trash2 />} danger onSelect={() => { setMenuOpen(false); setDeleting(true) }}>
            Delete group
          </MenuItem>
        </PopoverContent>
      </Popover>

      <Dialog open={renaming} onOpenChange={setRenaming}>
        <DialogContent className="border-white/10 bg-[#0d0f12] text-zinc-100">
          <DialogHeader><DialogTitle className="text-white">Rename group</DialogTitle></DialogHeader>
          <form
            className="space-y-4 pt-2"
            onSubmit={(e) => { e.preventDefault(); if (renameValue && !renameMut.isPending) renameMut.mutate() }}
          >
            <Input autoFocus value={renameValue} onChange={(e) => setRenameValue(e.target.value)} className="border-white/10 bg-white/5 text-zinc-100" />
            <Button type="submit" className="w-full" disabled={!renameValue || renameMut.isPending}>Save</Button>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleting} onOpenChange={setDeleting}>
        <AlertDialogContent className="border-white/10 bg-[#0d0f12] text-zinc-100">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-white">Delete &ldquo;{groupName}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription className="text-zinc-400">
              Workflows in this group move to Default. Captured app sessions are removed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-white/10 bg-white/5 text-zinc-200">Cancel</AlertDialogCancel>
            <AlertDialogAction className="bg-red-600 text-white hover:bg-red-700" disabled={deleteMut.isPending} onClick={() => deleteMut.mutate()}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
