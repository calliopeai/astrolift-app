"use client";

import {
  ArrowRightIcon,
  BellIcon,
  BellOffIcon,
  PlusIcon,
  Trash2Icon,
  VolumeOffIcon,
  Volume2Icon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { adminCrumbs } from "@/components/screens/administration/insights/header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem, DropdownMenuSeparator } from "@/components/ui/dropdown-menu";
import { useFormatters } from "@/lib/i18n/formatters";

import { formatRemaining } from "./alert-format";
import { CreateAlertRuleSheet } from "./CreateAlertRuleSheet";
import { MuteAlertRuleSheet } from "./MuteAlertRuleSheet";
import type { AlertRule, useAlerts } from "./use-alerts";

export type AlertsScreenProps = ReturnType<typeof useAlerts>;

/**
 * Admin › Alerts (spec 44 §5.1): the rules on the shared list, views All ·
 * Mine · Active · Muted, cursor paged, mute and delete in each row's `⋯`.
 * The firing events are a Feed on their own route (/alerts/events); the
 * counts above the filters link to it. Pure; the data half is useAlerts.
 */
export function AlertsScreen({
  list,
  rows,
  totalCount,
  nextCursor,
  loading,
  error,
  onRetry,
  activeRuleCount,
  unresolvedCount,
  busy,
  createRule,
  deleteRule,
  mutePreset,
  muteCustom,
  unmute,
}: AlertsScreenProps) {
  const t = useTranslations("lists.alerts");
  const fmt = useFormatters();
  const [createOpen, setCreateOpen] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<AlertRule | null>(null);
  const [muteTarget, setMuteTarget] = React.useState<AlertRule | null>(null);

  // The rule state icon rides in the name cell rather than in a column of
  // its own: `rowHref` stretches a link over the first cell, and an
  // icon-only first cell would leave that link with no accessible name.
  const ruleColumns: Column<AlertRule>[] = [
    {
      id: "name",
      header: t("rules.columns.name"),
      cellClassName: "max-w-80",
      cell: (r) => (
        <span className="flex min-w-0 items-center gap-2 font-medium">
          {r.activeMute ? (
            <VolumeOffIcon className="text-muted-foreground size-4 shrink-0" />
          ) : r.isActive ? (
            <BellIcon className="text-success-fg size-4 shrink-0" />
          ) : (
            <BellOffIcon className="text-muted-foreground size-4 shrink-0" />
          )}
          <span className="min-w-0 truncate" title={r.name}>
            {r.name}
          </span>
          {r.activeMute && (
            <Badge variant="secondary" className="text-2xs">
              {t("mute.badge", { remaining: formatRemaining(r.activeMute.ttlUntil) })}
            </Badge>
          )}
        </span>
      ),
    },
    {
      id: "target",
      header: t("rules.columns.target"),
      cellClassName: "max-w-64",
      cell: (r) => (
        <span className="flex min-w-0 items-center gap-2">
          <Badge variant="outline">{r.target}</Badge>
          {r.targetId && (
            <span
              className="text-muted-foreground text-2xs min-w-0 truncate font-mono"
              title={r.targetId}
            >
              {r.targetId}
            </span>
          )}
        </span>
      ),
    },
    {
      id: "severity",
      header: t("rules.columns.severity"),
      cell: (r) => (
        <Badge variant="secondary" className="capitalize">
          {r.severity}
        </Badge>
      ),
    },
    {
      id: "createdAt",
      header: t("rules.columns.created"),
      cell: (r) => (
        <span className="text-muted-foreground font-mono text-xs">
          {fmt.formatDate(r.createdAt)}
        </span>
      ),
    },
  ];

  function rowActions(r: AlertRule) {
    return (
      <Can permission="org.update">
        {r.activeMute ? (
          <DropdownMenuItem disabled={busy} onSelect={() => void unmute(r)}>
            <Volume2Icon className="size-4" />
            {t("mute.unmute")}
          </DropdownMenuItem>
        ) : (
          <>
            <DropdownMenuItem disabled={busy} onSelect={() => void mutePreset(r, 1, "1h")}>
              <VolumeOffIcon className="size-4" />
              {t("mute.preset1h")}
            </DropdownMenuItem>
            <DropdownMenuItem disabled={busy} onSelect={() => void mutePreset(r, 4, "4h")}>
              <VolumeOffIcon className="size-4" />
              {t("mute.preset4h")}
            </DropdownMenuItem>
            <DropdownMenuItem disabled={busy} onSelect={() => void mutePreset(r, 24, "24h")}>
              <VolumeOffIcon className="size-4" />
              {t("mute.preset24h")}
            </DropdownMenuItem>
            <DropdownMenuItem disabled={busy} onSelect={() => setMuteTarget(r)}>
              <VolumeOffIcon className="size-4" />
              {t("mute.presetCustom")}
            </DropdownMenuItem>
          </>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled={busy} onSelect={() => setDeleteTarget(r)} variant="destructive">
          <Trash2Icon className="size-4" />
          {t("delete.confirm")}
        </DropdownMenuItem>
      </Can>
    );
  }

  // The counts sit above the filters; the unresolved one opens the feed.
  const counts = (
    <div className="grid min-w-0 gap-3 sm:grid-cols-2">
      <div className="min-w-0 rounded-md border p-3">
        <p className="text-muted-foreground text-xs tracking-wide uppercase">{t("stats.rules")}</p>
        <p className="mt-1 font-mono text-2xl font-bold">{totalCount ?? "—"}</p>
        <p className="text-muted-foreground text-xs">
          {t("stats.active", { count: activeRuleCount })}
        </p>
      </div>
      <Link
        href="/alerts/events?view=firing"
        className="hover:bg-muted/50 focus-visible:ring-ring block min-w-0 rounded-md border p-3 focus-visible:ring-2 focus-visible:outline-none"
      >
        <p className="text-muted-foreground flex items-center justify-between gap-2 text-xs tracking-wide uppercase">
          {t("stats.unresolved")}
          <ArrowRightIcon className="size-3.5" aria-hidden />
        </p>
        <p className="text-destructive mt-1 font-mono text-2xl font-bold">{unresolvedCount}</p>
        <p className="text-muted-foreground text-xs">{t("stats.inWindow")}</p>
      </Link>
    </div>
  );

  return (
    <>
      <ListPage<AlertRule>
        header={{
          crumbs: adminCrumbs("alerts", t("title")),
          title: t("title"),
          context: t("description"),
          primaryAction: (
            <div className="flex items-center gap-2">
              <Button asChild variant="outline">
                <Link href="/alerts/events">
                  <BellIcon className="size-4" />
                  {t("events.title")}
                </Link>
              </Button>
              <Can permission="org.update">
                <Button onClick={() => setCreateOpen(true)}>
                  <PlusIcon className="size-4" />
                  {t("newRule")}
                </Button>
              </Can>
            </div>
          ),
        }}
        notice={counts}
        list={list}
        label="Alert rules"
        columns={ruleColumns}
        rows={rows}
        getRowId={(r) => r.id}
        rowHref={(r) => `/alerts/rules/${r.id}`}
        rowActions={rowActions}
        rowClassName={(r) => (r.activeMute ? "opacity-70" : undefined)}
        loading={loading}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        nextCursor={nextCursor}
        empty={{
          icon: <BellIcon className="size-5" />,
          title: t("rules.emptyTitle"),
          description: t("rules.emptyDescription"),
        }}
      />

      <CreateAlertRuleSheet
        open={createOpen}
        onOpenChange={setCreateOpen}
        onSubmit={async (input) => {
          const ok = await createRule(input);
          if (ok) setCreateOpen(false);
          return ok;
        }}
        busy={busy}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={
          deleteTarget ? t("delete.title", { name: deleteTarget.name }) : t("delete.fallbackTitle")
        }
        description={t("delete.description")}
        confirmLabel={t("delete.confirm")}
        destructive
        onConfirm={async () => {
          if (deleteTarget) await deleteRule(deleteTarget);
        }}
      />

      <MuteAlertRuleSheet
        key={muteTarget?.id ?? "none"}
        target={muteTarget}
        onOpenChange={(next) => {
          if (!next) setMuteTarget(null);
        }}
        onSubmit={async (durationSeconds, reason) => {
          if (muteTarget) {
            const ok = await muteCustom(muteTarget, durationSeconds, reason);
            if (ok) setMuteTarget(null);
          }
        }}
        busy={busy}
      />
    </>
  );
}
