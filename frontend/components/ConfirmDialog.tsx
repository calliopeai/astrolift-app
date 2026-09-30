"use client";

import * as React from "react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

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

/** A required free-form reason, for actions the audit trail must explain. */
export interface ConfirmReason {
  /** Label above the textarea (e.g. "Reason for rejection"). */
  label: React.ReactNode;
  placeholder?: string;
  /** Shown inline on an empty reason. Defaults to "Reason required". */
  requiredError?: string;
}

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
  /** Block confirmation until required supporting data is available. */
  confirmDisabled?: boolean;
  /** Awaited when the user confirms. While the returned Promise is
   *  pending, both buttons are disabled and the confirm label switches
   *  to a "working" state. On resolve, the dialog closes. On reject, the
   *  dialog stays open and a sonner toast surfaces the error message so
   *  the operator can correct and retry. */
  onConfirm: (reason: string) => Promise<unknown> | unknown;
  /** Ask for a required reason (reject a deploy, abort a rollout). The
   *  trimmed reason is passed to `onConfirm`; an empty one is refused
   *  inline without calling it. Without this, `onConfirm` gets "". */
  reason?: ConfirmReason;
}

/**
 * Shared confirmation dialog for destructive actions, with an optional
 * required reason (the former ConfirmDialogWithReason, #2126). Replaces the
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
  confirmLabel,
  cancelLabel,
  destructive = false,
  confirmDisabled = false,
  onConfirm,
  reason,
}: ConfirmDialogProps) {
  const t = useTranslations("shared.confirmation");
  const [pending, setPending] = React.useState(false);
  const [draft, setDraft] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const reasonId = React.useId();

  // Cleared on every close, so a fresh open never shows the draft of a
  // cancelled attempt.
  function close() {
    setDraft("");
    setError(null);
    onOpenChange(false);
  }

  // We don't reset `pending` on `open` flips — the only writer is
  // `handleConfirm` and it always clears the flag in `finally`. The
  // wrapped `onOpenChange` below also blocks external closes while a
  // confirm is in flight, so the flag can't get stuck on.

  async function handleConfirm(e: React.MouseEvent) {
    // We swap radix's auto-close behavior for a manual flow so we can
    // hold the dialog open while the mutation is in flight and re-open
    // if it throws.
    e.preventDefault();
    if (pending || confirmDisabled) return;
    const trimmed = draft.trim();
    if (reason && !trimmed) {
      setError(reason.requiredError ?? t("reasonRequired"));
      return;
    }
    setError(null);
    setPending(true);
    try {
      await onConfirm(trimmed);
      close();
    } catch (err) {
      const message =
        err instanceof Error && err.message
          ? err.message
          : typeof err === "string" && err
            ? err
            : t("failed");
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
        if (next) onOpenChange(true);
        else close();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description != null && <AlertDialogDescription>{description}</AlertDialogDescription>}
        </AlertDialogHeader>
        {reason && (
          <div className="grid min-w-0 gap-2">
            <Label htmlFor={reasonId}>{reason.label}</Label>
            <Textarea
              id={reasonId}
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                if (error) setError(null);
              }}
              placeholder={reason.placeholder}
              rows={4}
              disabled={pending}
              aria-invalid={error != null}
              aria-describedby={error ? `${reasonId}-error` : undefined}
            />
            {error && (
              <p id={`${reasonId}-error`} className="text-destructive text-xs" role="alert">
                {error}
              </p>
            )}
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>{cancelLabel ?? t("cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant={destructive ? "destructive" : "default"}
            disabled={pending || confirmDisabled}
            onClick={handleConfirm}
          >
            {pending ? t("working") : (confirmLabel ?? t("confirm"))}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
