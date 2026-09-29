"use client";

import * as React from "react";

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

export interface AnonymizeUserDialogProps {
  /** The user being anonymized; null keeps the dialog closed. */
  name: string | null;
  onOpenChange: (open: boolean) => void;
  /** Resolves true on success; the hook toasts the reason on failure. */
  onConfirm: () => Promise<boolean>;
}

/**
 * Right to delete (GDPR Art. 17). Double confirm: the "I understand this is
 * irreversible" box enables the destructive button, which then has to be
 * clicked. The dialog stays open while the mutation runs, and on failure,
 * so the operator can retry. What is scrubbed and what stays is spelled out
 * so the blast radius is checked before acting (`core/anonymization.py` is
 * the source of truth).
 */
export function AnonymizeUserDialog({ name, onOpenChange, onConfirm }: AnonymizeUserDialogProps) {
  const [pending, setPending] = React.useState(false);
  const [acknowledged, setAcknowledged] = React.useState(false);
  const open = name !== null;

  async function handleConfirm(e: React.MouseEvent) {
    e.preventDefault();
    if (pending || !acknowledged) return;
    setPending(true);
    try {
      if (await onConfirm()) onOpenChange(false);
    } finally {
      setPending(false);
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (pending && !next) return;
        if (!next) setAcknowledged(false);
        onOpenChange(next);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="[overflow-wrap:anywhere]">
            Anonymize {name}?
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-4">
              <div>
                <p className="text-foreground mb-1.5 text-xs font-medium tracking-wide uppercase">
                  This will scrub
                </p>
                <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
                  <li>
                    <code className="bg-muted rounded px-1 font-mono">email</code> → SHA-256 hash,
                    not reversible
                  </li>
                  <li>
                    First and last name →{" "}
                    <code className="bg-muted rounded px-1 font-mono">[redacted]</code>
                  </li>
                  <li>Username → deterministic placeholder bound to the user ID</li>
                  <li>Phone, avatar URL → removed</li>
                  <li>Past audit-event payloads → IP, user-agent, email scrubbed in place</li>
                </ul>
              </div>
              <div>
                <p className="text-foreground mb-1.5 text-xs font-medium tracking-wide uppercase">
                  This preserves
                </p>
                <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
                  <li>
                    Audit-log structural records (timestamps, action types, affected resources)
                  </li>
                  <li>FK relationships from past actions, so referential integrity stays intact</li>
                  <li>
                    Member <code className="bg-muted rounded px-1 font-mono">lifecycle</code> flips
                    to <code className="bg-muted rounded px-1 font-mono">anonymized</code>; role
                    bindings are revoked
                  </li>
                </ul>
              </div>
              <p className="text-destructive font-medium">
                <strong>This action is irreversible.</strong> Re-running it on the same user is a
                no-op; the original PII cannot be restored.
              </p>
              <label className="flex items-start gap-2 rounded-md border p-3 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4"
                  checked={acknowledged}
                  onChange={(e) => setAcknowledged(e.target.checked)}
                  disabled={pending}
                />
                <span>I understand this is irreversible.</span>
              </label>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={pending || !acknowledged}
            onClick={handleConfirm}
          >
            {pending ? "Anonymizing…" : "Anonymize user data"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
