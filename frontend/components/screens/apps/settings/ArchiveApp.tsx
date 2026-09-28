"use client";

import { ArchiveIcon, Loader2Icon, PlayCircleIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useArchiveApp } from "./use-archive-app";

export type ArchiveAppViewProps = ReturnType<typeof useArchiveApp> & {
  appName: string;
  isArchived: boolean;
  archivedAt: string | null;
};

/**
 * Archive / restore, a row of the Settings tab's Danger zone (spec 44
 * §5.3): a reversible alternative to deregister that scales every workload
 * to zero and suppresses deploys. Archive asks first, in a ConfirmDialog;
 * restore puts things back, so it does not.
 */
export function ArchiveAppView({
  archiving,
  restoring,
  onArchive,
  onRestore,
  appName,
  isArchived,
  archivedAt,
}: ArchiveAppViewProps) {
  const fmt = useFormatters();
  const t = useTranslations("apps.frame.archive");
  const [confirmOpen, setConfirmOpen] = React.useState(false);

  return (
    <div className="flex min-w-0 flex-col gap-3 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="flex min-w-0 flex-wrap items-center gap-2 text-sm font-medium">
          App archive
          {isArchived ? (
            <Badge
              variant="outline"
              className="border-warning-border bg-warning/10 text-warning-fg"
            >
              Archived
            </Badge>
          ) : null}
        </p>
        <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">
          {isArchived
            ? archivedAt
              ? `Archived ${fmt.formatRelativeTime(archivedAt)}. Workloads are at zero replicas; deploys are suppressed.`
              : "App is archived. Workloads are at zero replicas."
            : "Archiving scales all workloads to zero and suppresses deploys. Restore returns replicas to their pre-archive counts."}
        </p>
      </div>
      <Can permission="app.update">
        {isArchived ? (
          <Button
            type="button"
            size="sm"
            className="shrink-0"
            disabled={restoring}
            onClick={() => void onRestore()}
          >
            {restoring ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <PlayCircleIcon className="size-3.5" />
            )}
            Restore
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            variant="destructive"
            className="shrink-0"
            disabled={archiving}
            onClick={() => setConfirmOpen(true)}
          >
            {archiving ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <ArchiveIcon className="size-3.5" />
            )}
            Archive
          </Button>
        )}
      </Can>
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={<span className="[overflow-wrap:anywhere]">{t("title", { name: appName })}</span>}
        description={t("description")}
        confirmLabel={t("confirm")}
        destructive
        // The hook toasts its own failure, so the dialog just closes.
        onConfirm={() => onArchive()}
      />
    </div>
  );
}
