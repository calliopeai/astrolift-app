"use client";

import { ExternalLinkIcon, ListChecksIcon, MonitorPlayIcon, TerminalIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { useTranslations } from "next-intl";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { VncViewer } from "@/components/observability/VncViewer";
import { StatusDot } from "@/components/StatusDot";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useFormatters } from "@/lib/i18n/formatters";

import { canWatchLive, canWatchLogs, runDot, runDuration, RUN_STATUS_DOT } from "./agent-runs-list";
import type { AgentRunState, AgentTask } from "./use-agent-run";

export interface AgentRunScreenProps extends AgentRunState {
  /**
   * The live log tail for a headless run, shown in the Watch logs dialog.
   * A slot, so its polling hook runs only while the dialog is open;
   * `running` tracks the poll so the tail stops once the run ends.
   */
  renderLogs: (taskId: string, running: boolean) => React.ReactNode;
}

const runHref = (task: AgentTask) => `/agents/runs/${encodeURIComponent(task.id)}`;

function Time({ at }: { at: string | null }) {
  const t = useTranslations("agentRunTab");
  const format = useFormatters();
  if (!at) return <span className="text-muted-foreground">{t("notYet")}</span>;
  return (
    <span className="font-mono text-xs" title={at}>
      {Number.isFinite(Date.parse(at)) ? format.formatRelativeTime(at) : t("unknownDate")}
    </span>
  );
}

const columnsFor = (
  t: (key: string) => string,
  activity: (key: string) => string
): Column<AgentTask>[] => {
  return [
    {
      id: "run",
      header: t("run"),
      cell: (task) => (
        <span className="block max-w-72 truncate font-mono text-xs" title={task.id}>
          {task.id}
        </span>
      ),
    },
    {
      id: "status",
      header: t("status"),
      width: "w-40",
      cell: (task) => (
        <span className="inline-flex min-w-0 items-center gap-1.5 text-sm">
          <StatusDot status={runDot(task.status)} />
          <span className="truncate" title={task.status}>
            {task.status.toLowerCase() === "running"
              ? t("runningNow")
              : Object.hasOwn(RUN_STATUS_DOT, task.status.toLowerCase())
                ? activity(`statuses.${task.status.toLowerCase()}`)
                : task.status}
          </span>
        </span>
      ),
    },
    {
      id: "started",
      header: t("started"),
      width: "w-28",
      cell: (task) => <Time at={task.startedAt} />,
    },
    {
      id: "finished",
      header: t("finished"),
      width: "w-28",
      cell: (task) => <Time at={task.finishedAt} />,
    },
    {
      id: "took",
      header: t("took"),
      width: "w-20",
      align: "right",
      cellClassName: "font-mono text-xs tabular-nums",
      cell: (task) => runDuration(task.startedAt, task.finishedAt) ?? "",
    },
  ];
};

/**
 * The agent's Runs tab (spec 44 §5.1, §5.2): this agent's executions on the
 * embedded list, newest first, by cursor, with new runs held behind the
 * pill. The whole row opens the run; `⋯` watches a running one live (its
 * VNC session, or the log tail for a headless agent). Run now is the
 * frame's primary action, so the tab has none of its own. Pure.
 */
export function AgentRunScreen({
  list,
  rows,
  newRows,
  loading,
  stale,
  error,
  onRetry,
  nextCursor,
  totalCount,
  renderLogs,
}: AgentRunScreenProps) {
  const t = useTranslations("agentRunTab");
  const activity = useTranslations("agentActivity");
  const columns = React.useMemo(() => columnsFor(t, activity), [t, activity]);
  const [watching, setWatching] = React.useState<AgentTask | null>(null);
  const [watchingLogs, setWatchingLogs] = React.useState<AgentTask | null>(null);

  // The open log dialog follows the poll, so its tail stops the moment the run ends.
  const logsLive = watchingLogs ? (rows.find((r) => r.id === watchingLogs.id) ?? null) : null;

  return (
    <>
      <ListPage<AgentTask>
        embedded
        list={list}
        label={t("runs")}
        columns={columns}
        rows={rows}
        getRowId={(task) => task.id}
        rowHref={runHref}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        nextCursor={nextCursor}
        totalCount={totalCount}
        newRows={newRows}
        empty={{
          icon: <ListChecksIcon className="size-5" />,
          title: t("noRuns"),
          description: t("noRunsDescription"),
        }}
        rowActions={(task) => (
          <>
            <DropdownMenuItem asChild>
              <Link href={runHref(task)}>{t("openRun")}</Link>
            </DropdownMenuItem>
            {canWatchLive(task) && (
              <>
                <DropdownMenuItem onSelect={() => setWatching(task)}>
                  <MonitorPlayIcon className="size-4" />
                  {t("watchLive")}
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href={`${runHref(task)}/vnc`} target="_blank" rel="noreferrer">
                    <ExternalLinkIcon className="size-4" />
                    {t("openLive")}
                  </Link>
                </DropdownMenuItem>
              </>
            )}
            {canWatchLogs(task) && (
              <DropdownMenuItem onSelect={() => setWatchingLogs(task)}>
                <TerminalIcon className="size-4" />
                {t("watchLogs")}
              </DropdownMenuItem>
            )}
          </>
        )}
      />

      <Dialog open={watching !== null} onOpenChange={(open) => !open && setWatching(null)}>
        <DialogContent className="max-w-4xl sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>{t("sessionTitle")}</DialogTitle>
            <DialogDescription className="font-mono text-xs break-all">
              {watching?.id}
            </DialogDescription>
          </DialogHeader>
          {watching && <VncViewer vncPath={watching.vncUrl} />}
        </DialogContent>
      </Dialog>

      <Dialog open={watchingLogs !== null} onOpenChange={(open) => !open && setWatchingLogs(null)}>
        <DialogContent className="flex h-[92vh] w-[96vw] max-w-[96vw] flex-col gap-3 sm:max-w-[96vw]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <TerminalIcon className="size-4" />
              {t("logsTitle")}
            </DialogTitle>
            <DialogDescription className="font-mono text-xs break-all">
              {watchingLogs?.id}
            </DialogDescription>
          </DialogHeader>
          {watchingLogs && renderLogs(watchingLogs.id, logsLive?.status === "running")}
        </DialogContent>
      </Dialog>
    </>
  );
}
