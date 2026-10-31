"use client";

import { AlertTriangleIcon } from "lucide-react";
import { useTranslations } from "next-intl";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** An app environment whose platform hostname is under the zone. */
export interface ZoneDependent {
  organizationSlug: string;
  appSlug: string;
  environmentName: string;
  url: string;
}

export interface DefaultZoneDeleteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  zone: string;
  /** The first environments bound to the zone, as the server lists them. */
  dependents: ZoneDependent[];
  /** How many environments are bound in all; `dependents` holds the first ten. */
  total: number;
  /** The server's reason, when it kept the zone on a removal without force. */
  refusal?: string | null;
  busy?: boolean;
  /** The forced removal. Resolves true once the server accepted it. */
  onConfirm: () => Promise<boolean>;
}

/**
 * Removing the zone new apps get their hostnames under (#2572). An
 * AlertDialog of its own rather than ConfirmDialog, like the app's
 * deregister: it lists what resolves through the zone and arms the forced
 * removal only once the zone is typed.
 */
export function DefaultZoneDeleteDialog({
  open,
  onOpenChange,
  zone,
  dependents,
  total,
  refusal,
  busy = false,
  onConfirm,
}: DefaultZoneDeleteDialogProps) {
  const t = useTranslations("managedDomains");
  const working = useTranslations("shared.confirmation");
  const inputId = React.useId();
  const [typed, setTyped] = React.useState("");
  const [pending, setPending] = React.useState(false);
  const armed = typed.trim() === zone && !busy && !pending;

  function changeOpen(next: boolean) {
    if (pending) return;
    setTyped("");
    onOpenChange(next);
  }

  async function confirm() {
    if (!armed) return;
    setPending(true);
    let accepted = false;
    try {
      accepted = await onConfirm();
    } finally {
      setPending(false);
    }
    if (accepted) {
      setTyped("");
      onOpenChange(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={changeOpen}>
      <AlertDialogContent className="max-h-[90vh] overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <AlertTriangleIcon className="text-destructive size-5 shrink-0" />
            {t("defaultDeleteTitle")}
          </AlertDialogTitle>
          <AlertDialogDescription>{t("defaultDeleteHelp")}</AlertDialogDescription>
        </AlertDialogHeader>
        {refusal && (
          <div
            role="alert"
            className="border-danger-border bg-danger-bg text-danger-fg min-w-0 space-y-1 rounded-md border p-3 text-sm"
          >
            <p className="font-medium">{t("defaultDeleteRefused")}</p>
            <p className="[overflow-wrap:anywhere]">{refusal}</p>
          </div>
        )}
        <section aria-label={t("defaultDependentsLabel")} className="min-w-0 space-y-2 text-sm">
          <p>
            {total === 0
              ? t("defaultDependentsNone")
              : t("defaultDependentsCount", { count: total })}
          </p>
          {dependents.length > 0 && (
            <ul className="space-y-1.5 text-xs">
              {dependents.map((dependent) => (
                <li
                  key={`${dependent.organizationSlug}/${dependent.appSlug}/${dependent.environmentName}`}
                  className="min-w-0"
                >
                  <span className="block font-mono [overflow-wrap:anywhere]">
                    {dependent.appSlug} · {dependent.environmentName}
                  </span>
                  <span className="text-muted-foreground block [overflow-wrap:anywhere]">
                    {dependent.url
                      ? `${dependent.organizationSlug} · ${dependent.url}`
                      : dependent.organizationSlug}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {total > dependents.length && (
            <p className="text-muted-foreground text-xs">
              {t("defaultDependentsMore", { count: total - dependents.length })}
            </p>
          )}
        </section>
        <div className="grid min-w-0 gap-1.5">
          <Label htmlFor={inputId} className="block text-xs [overflow-wrap:anywhere]">
            {t("defaultDeleteTypeToConfirm")} <span className="font-mono">{zone}</span>
          </Label>
          <Input
            id={inputId}
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            disabled={busy || pending}
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>{t("cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={!armed}
            onClick={(event) => {
              event.preventDefault();
              void confirm();
            }}
          >
            {pending ? working("working") : t("defaultDeleteConfirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
