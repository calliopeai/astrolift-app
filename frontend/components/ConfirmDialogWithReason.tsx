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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface ConfirmDialogWithReasonProps {
  /** Controlled open state — pair with `onOpenChange`. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Label above the textarea (e.g. "Reason"). */
  reasonLabel: React.ReactNode;
  /** Placeholder rendered when the textarea is empty. */
  reasonPlaceholder?: string;
  /** Message shown inline when the user tries to confirm with an empty
   *  reason. Defaults to "Reason required". */
  reasonRequiredError?: string;
  confirmLabel?: React.ReactNode;
  cancelLabel?: React.ReactNode;
  /** Use the destructive button variant. Defaults to true for this
   *  component since the canonical use is reject / abort. */
  destructive?: boolean;
  /** Receives the trimmed reason and is awaited. On reject the dialog
   *  stays open and the error message surfaces as a toast. */
  onConfirm: (reason: string) => Promise<unknown> | unknown;
}

/**
 * Confirmation dialog with a required free-form reason textarea.
 *
 * Drop-in companion to ``ConfirmDialog`` for actions that need an
 * audit-trail explanation — reject deploy, abort in-flight rollout,
 * revoke token with a justification. The reason is trimmed before
 * being handed to ``onConfirm``; submitting with an empty/whitespace
 * value surfaces an inline error rather than firing the mutation.
 *
 *   <ConfirmDialogWithReason
 *     open={open}
 *     onOpenChange={setOpen}
 *     title="Reject deploy?"
 *     reasonLabel="Reason for rejection"
 *     reasonPlaceholder="Why are you rejecting this deploy?"
 *     destructive
 *     onConfirm={async (reason) => {
 *       const { data } = await reject({ variables: { input: { id, reason } } });
 *       if (!data?.rejectDeployment?.ok) {
 *         throw new Error(data?.rejectDeployment?.errors?.[0]?.message ?? "Reject failed");
 *       }
 *     }}
 *   />
 */
export function ConfirmDialogWithReason({
  open,
  onOpenChange,
  title,
  description,
  reasonLabel,
  reasonPlaceholder,
  reasonRequiredError = "Reason required",
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = true,
  onConfirm,
}: ConfirmDialogWithReasonProps) {
  const [reason, setReason] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [pending, setPending] = React.useState(false);

  // Reset the textarea + error whenever the dialog opens fresh so the
  // operator never sees a stale draft from a prior cancelled attempt.
  React.useEffect(() => {
    if (open) {
      setReason("");
      setError(null);
    }
  }, [open]);

  async function handleConfirm(e: React.MouseEvent) {
    e.preventDefault();
    if (pending) return;
    const trimmed = reason.trim();
    if (!trimmed) {
      setError(reasonRequiredError);
      return;
    }
    setError(null);
    setPending(true);
    try {
      await onConfirm(trimmed);
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
        if (pending && !next) return;
        onOpenChange(next);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description != null && <AlertDialogDescription>{description}</AlertDialogDescription>}
        </AlertDialogHeader>
        <div className="grid gap-2">
          <Label htmlFor="confirm-reason">{reasonLabel}</Label>
          <Textarea
            id="confirm-reason"
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
              if (error) setError(null);
            }}
            placeholder={reasonPlaceholder}
            rows={4}
            disabled={pending}
            aria-invalid={error != null}
            aria-describedby={error ? "confirm-reason-error" : undefined}
          />
          {error && (
            <p id="confirm-reason-error" className="text-destructive text-xs" role="alert">
              {error}
            </p>
          )}
        </div>
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
