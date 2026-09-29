"use client";

import { TerminalIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { RunStatusBadge } from "@/components/jobs/RunStatusBadge";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { appsListCrumbs } from "@/components/screens/deployments/apps-area";
import type { AstroliftCommandRun } from "@/graphql/lifecycle/lifecycle.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { commandStatus, commandText } from "./jobs-list";
import type { useCommandRuns } from "./use-command-runs";

export type CommandRunsScreenProps = ReturnType<typeof useCommandRuns>;

/**
 * Apps › Scheduled jobs › Commands (spec 44 §4.4, §5.1): one-off commands
 * run against app workloads. Views All · Mine, cursor paged, live. Pure
 * view; the data half is useCommandRuns.
 */
export function CommandRunsScreen({
  list,
  rows,
  newRows,
  loading,
  stale,
  error,
  onRetry,
  nextCursor,
  totalCount,
}: CommandRunsScreenProps) {
  const t = useTranslations("jobs.commands");
  const fmt = useFormatters();

  const columns: Column<AstroliftCommandRun>[] = [
    {
      id: "command",
      header: t("columns.command"),
      cellClassName: "max-w-96",
      cell: (c) => (
        <span className="block min-w-0">
          <span
            className="block truncate font-mono text-sm font-medium"
            title={commandText(c.command)}
          >
            {commandText(c.command)}
          </span>
          <span className="text-muted-foreground block truncate text-xs">
            {c.registeredAppSlug}
            {c.workloadSlug && (
              <>
                {" · "}
                <span className="font-mono">{c.workloadSlug}</span>
              </>
            )}
          </span>
        </span>
      ),
    },
    {
      id: "status",
      header: t("columns.status"),
      cell: (c) => <RunStatusBadge status={commandStatus(c)} exitCode={c.exitCode} />,
    },
    {
      id: "invokedBy",
      header: t("columns.invokedBy"),
      cellClassName: "max-w-48",
      cell: (c) => (
        <span className="block truncate text-sm" title={c.invokedByUsername ?? undefined}>
          {c.invokedByUsername ?? "—"}
        </span>
      ),
    },
    {
      id: "started",
      header: t("columns.started"),
      cell: (c) => (
        <span className="text-muted-foreground font-mono text-xs">
          {c.startedAt ? fmt.formatDateTime(c.startedAt) : "—"}
        </span>
      ),
    },
  ];

  return (
    <ListPage<AstroliftCommandRun>
      header={{ crumbs: appsListCrumbs("jobs", { label: "Commands" }), title: "Command runs" }}
      list={list}
      label="Command runs"
      columns={columns}
      rows={rows}
      getRowId={(c) => c.id}
      rowHref={(c) => `/jobs/commands/${c.id}`}
      loading={loading}
      stale={stale}
      error={error}
      onRetry={onRetry}
      empty={{
        icon: <TerminalIcon className="size-5" />,
        title: t("emptyTitle"),
        description: t("emptyDescription"),
      }}
      totalCount={totalCount}
      nextCursor={nextCursor}
      newRows={newRows}
    />
  );
}
