"use client";

import { NetworkIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button, Skeleton } from "@calliopelabs/ui";

import type { useCustomDomainControl, useCustomDomains } from "./use-app-access";

export type CustomDomainsViewProps = ReturnType<typeof useCustomDomains> & {
  /** The pause control, for an operator holding `app.access`; read-only without it. */
  control?: ReturnType<typeof useCustomDomainControl>;
};

/**
 * The app's custom domains, each with whether it is serving and a control
 * to pause or resume it. A paused domain answers visitors with a 503 until
 * it is resumed. Pausing asks for confirmation; resuming never does.
 */
export function CustomDomainsView({ domains, loading, failed, control }: CustomDomainsViewProps) {
  const t = useTranslations("apps.security.domains");
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex items-center gap-2 text-sm font-medium">
        <NetworkIcon className="size-4" />
        {t("title")}
      </div>
      <p className="text-muted-foreground text-xs">{t("description")}</p>
      {loading && domains.length === 0 ? (
        <Skeleton className="h-8 w-full" />
      ) : failed ? (
        <p role="alert" className="text-destructive text-xs">
          {t("loadFailed")}
        </p>
      ) : domains.length === 0 ? (
        <p className="text-muted-foreground text-xs">{t("empty")}</p>
      ) : (
        <ul className="grid gap-2">
          {domains.map((d) => (
            <li key={d.id} className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-mono text-xs [overflow-wrap:anywhere]">{d.hostname}</span>
              <Badge
                variant={d.isPaused ? "destructive" : "secondary"}
                className="text-2xs font-normal"
              >
                {d.isPaused ? t("statePaused") : t("stateServing")}
              </Badge>
              {control && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="ml-auto"
                  disabled={control.saving}
                  onClick={() =>
                    d.isPaused
                      ? void control.onSet(d.id, false)
                      : control.setPausing({ id: d.id, hostname: d.hostname })
                  }
                >
                  {control.saving ? t("saving") : d.isPaused ? t("resume") : t("pause")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {control && (
        <>
          {control.refusal && (
            <p role="alert" className="text-destructive text-xs">
              {control.refusal}
            </p>
          )}
          <ConfirmDialog
            open={control.pausing !== null}
            onOpenChange={(open) => {
              if (!open) control.setPausing(null);
            }}
            title={t("confirmTitle", { hostname: control.pausing?.hostname ?? "" })}
            description={t("confirmDescription")}
            confirmLabel={t("confirm")}
            destructive
            // Closes either way: a refusal shows under the list, not
            // behind the modal.
            onConfirm={async () => {
              if (control.pausing) await control.onSet(control.pausing.id, true);
            }}
          />
        </>
      )}
    </div>
  );
}
