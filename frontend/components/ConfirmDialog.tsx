"use client";

import * as React from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

interface ConfirmDialogProps {
  /** Controlled open state — pair with `onOpenChange`. */
  open: boolean;
  /** Called whenever the dialog's open state changes (cancel, backdrop, or
   *  successful confirm). On error the dialog stays open and this is not
   *  called. */
  onOpenChange: (open: boolean) => void;
  /** Title rendered at the top of the dialog. */
  title: React.ReactNode;
  /** Optional supporting copy below the title. Should explain the blast
   *  radius (what gets deleted, what's recoverable, who can undo). */
  description?: React.ReactNode;
  /** Label on the confirm button. Defaults to "Confirm". */
  confirmLabel?: React.ReactNode;
  /** Label on the cancel button. Defaults to "Cancel". */
  cancelLabel?: React.ReactNode;
  /** If true, the confirm button uses the destructive variant — the
   *  standard treatment for delete / revoke / soft-delete affordances. */
  destructive?: boolean;
  /** Awaited when the user confirms. While the returned Promise is
   *  pending, both buttons are disabled and the confirm label switches
   *  to a "working" state. On resolve, the dialog closes. On reject, the
   *  dialog stays open and a sonner toast surfaces the error message so
   *  the operator can correct and retry. */
  onConfirm: () => Promise<unknown> | unknown;
}

/**
 * Shared confirmation dialog for destructive actions. Replaces the
 * native browser `confirm()` so the prompt matches the rest of the UI
 * (centered AlertDialog, themed buttons, destructive accent) and so we
 * can show real error feedback when the underlying mutation fails
 * instead of losing it to a bare boolean.
 *
 *   const [open, setOpen] = React.useState(false);
 *   <Button onClick={() => setOpen(true)}>Delete</Button>
 *   <ConfirmDialog
 *     open={open}
 *     onOpenChange={setOpen}
 *     title="Delete app?"
 *     description="Soft delete — recoverable for 30 days by an org owner."
 *     confirmLabel="Delete app"
 *     destructive
 *     onConfirm={async () => {
 *       const { data } = await softDelete({ variables: ... });
 *       if (!data?.softDelete.ok) {
 *         throw new Error(data?.softDelete.errors?.[0]?.message ?? "Delete failed");
 *       }
 *     }}
 *   />
 *
 * The imperative `useConfirm()` helper in `hooks/use-confirm.tsx`
 * remains available for one-off call sites that don't have local state
 * to wire into; prefer this declarative form for table rows and detail
 * pages where the state is colocated with the action.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  onConfirm,
}: ConfirmDialogProps) {
  const [pending, setPending] = React.useState(false);

  // We don't reset `pending` on `open` flips — the only writer is
  // `handleConfirm` and it always clears the flag in `finally`. The
  // wrapped `onOpenChange` below also blocks external closes while a
  // confirm is in flight, so the flag can't get stuck on.

  async function handleConfirm(e: React.MouseEvent) {
    // We swap radix's auto-close behavior for a manual flow so we can
    // hold the dialog open while the mutation is in flight and re-open
    // if it throws.
    e.preventDefault();
    if (pending) return;
    setPending(true);
    try {
      await onConfirm();
      onOpenChange(false);
    } catch (err) {
      const message =
        err instanceof Error && err.message
          ? err.message
          : typeof err === "string" && err
            ? err
            : "Action failed";
      toast.error(message);
    } finally {
      setPending(false);
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        // Block the implicit close while a mutation is in flight — the
        // user can still cancel by clicking the explicit Cancel button
        // once it re-enables, but a stray backdrop click shouldn't
        // abandon a running request.
        if (pending && !next) return;
        onOpenChange(next);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description != null && <AlertDialogDescription>{description}</AlertDialogDescription>}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>{cancelLabel}</AlertDialogCancel>
          <AlertDialogAction
            variant={destructive ? "destructive" : "default"}
            disabled={pending}
            onClick={handleConfirm}
          >
            {pending ? "Working…" : confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
