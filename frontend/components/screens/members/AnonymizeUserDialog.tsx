"use client";

import * as React from "react";
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

export interface AnonymizeUserDialogProps {
  /** The user being anonymized; null keeps the dialog closed. */
  name: string | null;
  /** The connected user GUID; older presentation-only callers may omit it. */
  targetId?: string | null;
  /** Only the authoritative server flag establishes an existing anonymous account. */
  isAnonymized?: boolean | null;
  onOpenChange: (open: boolean) => void;
  /** Resolves true on success; the hook toasts the reason on failure. */
  onConfirm: () => Promise<boolean>;
}

/**
 * Irreversible account cleanup. Double confirm: the "I understand this is
 * irreversible" box enables the destructive button, which then has to be
 * clicked. The dialog stays open while the mutation runs, and on failure,
 * so the operator can retry. What is scrubbed and what stays is spelled out
 * so the operator can assess the effect before acting. The connected
 * `astrolift_identity/anonymize.py` and `Profile.anonymize_user` define
 * the actual behavior; unused pure helpers do not establish extra effects.
 */
export function AnonymizeUserDialog({
  name,
  targetId,
  isAnonymized,
  onOpenChange,
  onConfirm,
}: AnonymizeUserDialogProps) {
  const t = useTranslations("orgMembers.anonymization");
  const shared = useTranslations("shared.confirmation");
  const [pending, setPending] = React.useState(false);
  const identity = targetId ?? name;
  const anonymous = isAnonymized === true;
  const [acknowledgement, setAcknowledgement] = React.useState({
    identity,
    name,
    anonymous,
    checked: false,
  });
  if (
    acknowledgement.identity !== identity ||
    acknowledgement.name !== name ||
    acknowledgement.anonymous !== anonymous
  ) {
    setAcknowledgement({ identity, name, anonymous, checked: false });
  }
  const acknowledged =
    acknowledgement.identity === identity &&
    acknowledgement.name === name &&
    acknowledgement.anonymous === anonymous &&
    acknowledgement.checked;
  const currentTarget = React.useRef({ identity, name, anonymous });
  React.useLayoutEffect(() => {
    currentTarget.current = { identity, name, anonymous };
  }, [identity, name, anonymous]);
  const open = name !== null;

  async function handleConfirm(e: React.MouseEvent) {
    e.preventDefault();
    if (pending || !acknowledged) return;
    setPending(true);
    const reviewedTarget = currentTarget.current;
    try {
      if ((await onConfirm()) && reviewedTarget === currentTarget.current) onOpenChange(false);
    } finally {
      setPending(false);
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (pending && !next) return;
        if (!next) setAcknowledgement({ identity, name: null, anonymous, checked: false });
        onOpenChange(next);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="[overflow-wrap:anywhere]">
            {t(anonymous ? "reviewTitle" : "title", { name: name ?? "" })}
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-4">
              <div>
                <p className="text-foreground mb-1.5 text-xs font-medium tracking-wide uppercase">
                  {t("changes")}
                </p>
                <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
                  {anonymous ? (
                    <li>{t("anonymousIdentity")}</li>
                  ) : (
                    <>
                      <li>{t("identity")}</li>
                      <li>{t("profile")}</li>
                      <li>{t("account")}</li>
                      <li>{t("memberships")}</li>
                    </>
                  )}
                  <li>{t("history")}</li>
                  <li>{t("metadata")}</li>
                </ul>
              </div>
              <div>
                <p className="text-foreground mb-1.5 text-xs font-medium tracking-wide uppercase">
                  {t("preserves")}
                </p>
                <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
                  <li>{t("records")}</li>
                  <li>{t("roleBindings")}</li>
                  <li>{t("scopeLimits")}</li>
                  <li>{t("external")}</li>
                </ul>
              </div>
              <p className="text-destructive font-medium">{t("irreversible")}</p>
              <label className="flex items-start gap-2 rounded-md border p-3 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4"
                  checked={acknowledged}
                  onChange={(e) =>
                    setAcknowledgement({ identity, name, anonymous, checked: e.target.checked })
                  }
                  disabled={pending}
                />
                <span>{t("acknowledge")}</span>
              </label>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>{shared("cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={pending || !acknowledged}
            onClick={handleConfirm}
          >
            {t(
              anonymous
                ? pending
                  ? "reviewWorking"
                  : "reviewConfirm"
                : pending
                  ? "working"
                  : "confirm"
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
