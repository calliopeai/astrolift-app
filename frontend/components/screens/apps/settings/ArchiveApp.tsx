"use client";

import { ArchiveIcon, Loader2Icon, PlayCircleIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useArchiveApp } from "./use-archive-app";

export type ArchiveAppViewProps = ReturnType<typeof useArchiveApp> & {
  appName: string;
  appId?: string;
  sourceVersion?: number | null;
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
  appId,
  sourceVersion,
  isArchived,
  archivedAt,
}: ArchiveAppViewProps) {
  const fmt = useFormatters();
  const t = useTranslations("apps.frame.archive");
  const copy = useTranslations("apps.settings.archiveFlow");
  const perms = useMyPermissions();
  const allowed = (perms.loading && perms.granted.size === 0) || perms.can("app.update");
  const fingerprint = JSON.stringify([
    appId,
    appName,
    sourceVersion,
    isArchived,
    archivedAt,
    allowed,
  ]);
  const context = React.useMemo(() => ({ fingerprint }), [fingerprint]);
  const current = React.useRef<object | null>(context);
  React.useLayoutEffect(() => {
    current.current = context;
    return () => {
      current.current = null;
    };
  }, [context]);
  const [review, setReview] = React.useState<object | null>(null);
  const confirmOpen = review === context && allowed && !isArchived;

  return (
    <div className="flex min-w-0 flex-col gap-3 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="flex min-w-0 flex-wrap items-center gap-2 text-sm font-medium">
          {copy("title")}
          {isArchived ? (
            <Badge
              variant="outline"
              className="border-warning-border bg-warning/10 text-warning-fg"
            >
              {copy("archived")}
            </Badge>
          ) : null}
        </p>
        <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">
          {isArchived
            ? archivedAt
              ? copy("archivedAt", { at: fmt.formatRelativeTime(archivedAt) })
              : copy("archivedWithoutTime")
            : copy("description")}
        </p>
      </div>
      <Can permission="app.update">
        {isArchived ? (
          <Button
            type="button"
            size="sm"
            className="shrink-0"
            disabled={restoring}
            onClick={() => {
              if (current.current === context && allowed) void onRestore();
            }}
          >
            {restoring ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <PlayCircleIcon className="size-3.5" />
            )}
            {copy("restore")}
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            variant="destructive"
            className="shrink-0"
            disabled={archiving}
            onClick={() => setReview(context)}
          >
            {archiving ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <ArchiveIcon className="size-3.5" />
            )}
            {copy("archive")}
          </Button>
        )}
      </Can>
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={(open) => {
          if (current.current === context)
            setReview((prior) => (open ? context : prior === context ? null : prior));
        }}
        title={<span className="[overflow-wrap:anywhere]">{t("title", { name: appName })}</span>}
        description={copy("description")}
        confirmLabel={t("confirm")}
        destructive
        onConfirm={async () => {
          if (current.current !== context || !allowed || review !== context) {
            return false;
          }
          const accepted = await onArchive();
          return current.current === context ? accepted : false;
        }}
      />
    </div>
  );
}
