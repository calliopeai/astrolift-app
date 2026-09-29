"use client";

import { ExternalLinkIcon, ListChecksIcon, MonitorPlayIcon, TerminalIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

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
import { formatRelativeAge } from "@/lib/format";

import { canWatchLive, canWatchLogs, runDot, runDuration, titleCase } from "./agent-runs-list";
import type { AgentRunState, AgentTask } from "./use-agent-run";

export interface AgentRunScreenProps extends AgentRunState {
  /**
   * The live log tail for a headless run, shown in the Watch logs dialog.
   * A slot, so its polling hook runs only while the dialog is open;
   * `running` tracks the poll so the tail stops once the run ends.
   */
  renderLogs: (taskId: string, running: boolean) => React.ReactNode;
}

const runHref = (t: AgentTask) => `/agents/runs/${encodeURIComponent(t.id)}`;

function Time({ at }: { at: string | null }) {
  if (!at) return <span className="text-muted-foreground">Not yet</span>;
  return (
    <span className="font-mono text-xs" title={at}>
      {formatRelativeAge(at)}
    </span>
  );
}

const COLUMNS: Column<AgentTask>[] = [
  {
    id: "run",
    header: "Run",
    cell: (t) => (
      <span className="block max-w-72 truncate font-mono text-xs" title={t.id}>
        {t.id}
      </span>
    ),
  },
  {
    id: "status",
    header: "Status",
    width: "w-40",
    cell: (t) => (
      <span className="inline-flex min-w-0 items-center gap-1.5 text-sm">
        <StatusDot status={runDot(t.status)} />
        <span className="truncate" title={t.status}>
          {t.status === "running" ? "Running now" : titleCase(t.status)}
        </span>
      </span>
    ),
  },
  { id: "started", header: "Started", width: "w-28", cell: (t) => <Time at={t.startedAt} /> },
  { id: "finished", header: "Finished", width: "w-28", cell: (t) => <Time at={t.finishedAt} /> },
  {
    id: "took",
    header: "Took",
    width: "w-20",
    align: "right",
    cellClassName: "font-mono text-xs tabular-nums",
    cell: (t) => runDuration(t.startedAt, t.finishedAt) ?? "",
  },
];

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
  const [watching, setWatching] = React.useState<AgentTask | null>(null);
  const [watchingLogs, setWatchingLogs] = React.useState<AgentTask | null>(null);

  // The open log dialog follows the poll, so its tail stops the moment the run ends.
  const logsLive = watchingLogs ? (rows.find((r) => r.id === watchingLogs.id) ?? null) : null;

  return (
    <>
      <ListPage<AgentTask>
        embedded
        list={list}
        label="Runs"
        columns={COLUMNS}
        rows={rows}
        getRowId={(t) => t.id}
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
          title: "No runs yet",
          description: "Use Run now above to start one. It shows here with a live status.",
        }}
        rowActions={(t) => (
          <>
            <DropdownMenuItem asChild>
              <Link href={runHref(t)}>Open run</Link>
            </DropdownMenuItem>
            {canWatchLive(t) && (
              <>
                <DropdownMenuItem onSelect={() => setWatching(t)}>
                  <MonitorPlayIcon className="size-4" />
                  Watch live
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href={`${runHref(t)}/vnc`} target="_blank" rel="noreferrer">
                    <ExternalLinkIcon className="size-4" />
                    Open live session in a new tab
                  </Link>
                </DropdownMenuItem>
              </>
            )}
            {canWatchLogs(t) && (
              <DropdownMenuItem onSelect={() => setWatchingLogs(t)}>
                <TerminalIcon className="size-4" />
                Watch logs
              </DropdownMenuItem>
            )}
          </>
        )}
      />

      <Dialog open={watching !== null} onOpenChange={(open) => !open && setWatching(null)}>
        <DialogContent className="max-w-4xl sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>Live agent session</DialogTitle>
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
              Live agent logs
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
