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

type DeleteStepDialogProps = {
  deleteIndex: number | null
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}

export function DeleteStepDialog({ deleteIndex, onOpenChange, onConfirm }: DeleteStepDialogProps) {
  return (
    <AlertDialog open={deleteIndex !== null} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove step {deleteIndex !== null ? deleteIndex + 1 : ''}?</AlertDialogTitle>
          <AlertDialogDescription>
            This removes the step from the workflow. You can undo it right after, or record that part again.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onConfirm}>
            Remove
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
