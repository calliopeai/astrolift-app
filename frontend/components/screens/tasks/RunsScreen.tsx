"use client";

import {
  ClipboardListIcon,
  ExternalLinkIcon,
  HistoryIcon,
  MonitorPlayIcon,
  MoreHorizontalIcon,
  PlayIcon,
  RotateCcwIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column, RowSelection } from "@/components/data-table";
import { Identifier } from "@/components/Identifier";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useFormatters } from "@/lib/i18n/formatters";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

import { RUN_KIND_LABEL, type RunOutcome, type RunRow } from "./runs-list";

export interface RunsScreenProps {
  /** List state: views, search, chips, cursor (see runs-list.ts). */
  list: ListStateController;
  /** Inside an agent's Runs tab: no header, the views become a picker. */
  embedded?: boolean;
  /** The page of runs, newest first. */
  rows: RunRow[];
  newRows?: { count: number; onReveal: () => void };
  loading: boolean;
  stale: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  nextCursor: string | null;
  totalCount: number | null;
  approximateCount: boolean;
  /** A run by key, from any page: the bulk bar acts on the selection. */
  lookup: (key: string) => RunRow | null;
  /** Cancels the runs; throws when none could be. Absent: no Cancel. */
  onCancel?: (runs: RunRow[]) => Promise<void>;
  /** Runs finished agent runs again; throws when none could be. Absent: no Retry. */
  onRetryRuns?: (runs: RunRow[]) => Promise<void>;
}

/** The Scheduled view lists firings to come, so its time column is when each fires. */
const upcomingView = (list: ListStateController) => Boolean(list.filters.upcoming);

const DOT: Record<RunOutcome, "ok" | "warn" | "error" | "muted" | "pending"> = {
  running: "pending",
  waiting: "warn",
  succeeded: "ok",
  failed: "error",
  cancelled: "muted",
  unknown: "muted",
};

export function formatTook(s: number | null): string {
  if (s === null) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

/** The run's outcome, and its own status word when that says more. */
export function RunOutcomeCell({ run }: { run: Pick<RunRow, "outcome" | "status"> }) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 font-mono text-xs">
      <StatusDot status={DOT[run.outcome]} />
      <span>{run.outcome}</span>
      {run.status && run.status !== run.outcome && (
        <span className="text-muted-foreground truncate" title={run.status}>
          · {run.status}
        </span>
      )}
    </span>
  );
}

/**
 * Agents › Runs (spec 44 §4.1, §4.4, §5.1): every agent run and workflow
 * run, with the container task runs, in one live list. Views All · Mine ·
 * Running · Failed · Waiting · Scheduled; chips for kind, status, agent,
 * workflow, project, trigger and since; newest first by cursor; new rows
 * wait behind the pill; running runs cancel and finished agent runs retry
 * in bulk. The Scheduled view lists the next firings of scheduled agents.
 * `embedded` is an agent's Runs tab, the same list under the agent's tabs.
 * Pure.
 */
export function RunsScreen({
  list,
  embedded = false,
  rows,
  newRows,
  loading,
  stale,
  error,
  onRetry,
  nextCursor,
  totalCount,
  approximateCount,
  lookup,
  onCancel,
  onRetryRuns,
}: RunsScreenProps) {
  const fmt = useFormatters();
  const [toCancel, setToCancel] = React.useState<{ runs: RunRow[]; clear?: () => void } | null>(
    null
  );
  const [toRetry, setToRetry] = React.useState<{ runs: RunRow[]; clear?: () => void } | null>(null);

  const columns: Column<RunRow>[] = [
    {
      id: "run",
      header: "Run",
      cellClassName: "max-w-48",
      cell: (r) =>
        r.upcoming ? (
          <div className="flex min-w-0 flex-col">
            <span className="truncate font-mono text-xs" title={r.id}>
              {r.id}
            </span>
            <span className="text-muted-foreground text-xs">Scheduled run</span>
          </div>
        ) : (
          <div className="flex min-w-0 flex-col">
            <Identifier value={r.id} kind="id" copyable={false} className="text-xs" />
            <span className="text-muted-foreground text-xs">{RUN_KIND_LABEL[r.kind]}</span>
          </div>
        ),
    },
    ...(embedded
      ? []
      : [
          {
            id: "subject",
            header: "What",
            cellClassName: "max-w-72",
            cell: (r: RunRow) => (
              <div className="flex min-w-0 flex-col">
                <span className="truncate font-mono text-sm" title={r.subject}>
                  {r.subject || "—"}
                </span>
                {(r.project || r.app) && (
                  <span
                    className="text-muted-foreground truncate font-mono text-xs"
                    title={r.project || r.app}
                  >
                    {r.project || r.app}
                  </span>
                )}
              </div>
            ),
          },
        ]),
    {
      id: "status",
      header: "Status",
      cell: (r) => <RunOutcomeCell run={r} />,
    },
    {
      id: "took",
      header: "Took",
      align: "right",
      cellClassName: "font-mono text-xs tabular-nums",
      cell: (r) => formatTook(r.durationSeconds),
    },
    {
      id: "trigger",
      header: "Trigger",
      cellClassName: "max-w-48",
      cell: (r) => (
        <div className="flex min-w-0 flex-col">
          <span className="truncate font-mono text-xs" title={r.trigger || undefined}>
            {r.trigger || <span className="text-muted-foreground">not recorded</span>}
          </span>
          {r.startedBy && (
            <span className="text-muted-foreground truncate text-xs" title={r.startedBy}>
              {r.startedBy}
            </span>
          )}
        </div>
      ),
    },
    {
      id: "at",
      header: upcomingView(list) ? "Fires" : "Started",
      sortKey: "at",
      cellClassName: "font-mono text-xs whitespace-nowrap",
      cell: (r) => (r.at ? fmt.formatDateTime(r.at) : "—"),
    },
  ];

  const bulkActions =
    onCancel || onRetryRuns
      ? (selection: RowSelection) => {
          const picked = selection.selectedIds.map(lookup).filter((r): r is RunRow => Boolean(r));
          const cancellable = picked.filter((r) => r.cancel);
          const retryable = picked.filter((r) => r.retry);
          return (
            <>
              {onRetryRuns && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={retryable.length === 0}
                  onClick={() => setToRetry({ runs: retryable, clear: selection.clear })}
                >
                  <RotateCcwIcon className="size-4" />
                  Retry{retryable.length > 0 ? ` ${retryable.length}` : ""}
                </Button>
              )}
              {onCancel && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={cancellable.length === 0}
                  onClick={() => setToCancel({ runs: cancellable, clear: selection.clear })}
                >
                  <XCircleIcon className="size-4" />
                  Cancel{cancellable.length > 0 ? ` ${cancellable.length}` : ""}
                </Button>
              )}
            </>
          );
        }
      : undefined;

  const rowActions = (r: RunRow) => (
    <>
      <DropdownMenuItem asChild>
        <Link href={r.href}>{r.upcoming ? "Open agent" : "Open run"}</Link>
      </DropdownMenuItem>
      {r.watchHref && (
        <DropdownMenuItem asChild>
          <Link href={r.watchHref} target="_blank" rel="noreferrer">
            <MonitorPlayIcon className="size-4" />
            Watch live
            <ExternalLinkIcon className="size-3.5" />
          </Link>
        </DropdownMenuItem>
      )}
      {onRetryRuns && r.retry && (
        <DropdownMenuItem onSelect={() => setToRetry({ runs: [r] })}>
          <RotateCcwIcon className="size-4" />
          Retry run
        </DropdownMenuItem>
      )}
      {onCancel && r.cancel && (
        <DropdownMenuItem onSelect={() => setToCancel({ runs: [r] })}>
          <XCircleIcon className="size-4" />
          Cancel run
        </DropdownMenuItem>
      )}
    </>
  );

  const body = {
    list,
    label: "Runs",
    columns,
    rows,
    getRowId: (r: RunRow) => r.key,
    rowHref: (r: RunRow) => r.href,
    rowActions,
    bulkActions,
    loading,
    stale,
    error,
    onRetry,
    empty: embedded
      ? {
          icon: <HistoryIcon className="size-5" />,
          title: "No runs yet",
          description: "This agent hasn't run yet. Run now starts one; it appears here live.",
        }
      : {
          icon: <HistoryIcon className="size-5" />,
          title: "No runs yet",
          description: "Agent runs, workflow runs and task runs appear here as they start.",
          actionHref: "/agents?tab=dispatch",
          actionLabel: "Run an agent",
        },
    totalCount,
    approximateCount,
    nextCursor,
    newRows,
  };

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3">
      {embedded ? (
        <ListPage<RunRow> embedded {...body} />
      ) : (
        <ListPage<RunRow>
          header={{
            crumbs: [areaSwitcher(NAV, "agents", "runs"), { label: "Runs" }],
            title: "Runs",
            primaryAction: (
              <Button asChild size="sm">
                <Link href="/agents?tab=dispatch">
                  <PlayIcon className="size-4" />
                  Run agent
                </Link>
              </Button>
            ),
            menu: (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" aria-label="More">
                    <MoreHorizontalIcon className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem asChild>
                    <Link href="/tasks/templates">
                      <ClipboardListIcon className="size-4" />
                      Task templates
                    </Link>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ),
          }}
          {...body}
        />
      )}

      {onRetryRuns && (
        <ConfirmDialog
          open={toRetry !== null}
          onOpenChange={(open) => !open && setToRetry(null)}
          title={
            toRetry && toRetry.runs.length > 1
              ? `Retry ${toRetry.runs.length} runs?`
              : "Retry this run?"
          }
          description="Each agent run starts again with the same brief, environment and inputs, as a new run you started. A run that could not start again says why."
          confirmLabel="Retry runs"
          onConfirm={async () => {
            if (!toRetry) return;
            await onRetryRuns(toRetry.runs);
            toRetry.clear?.();
          }}
        />
      )}

      {onCancel && (
        <ConfirmDialog
          open={toCancel !== null}
          onOpenChange={(open) => !open && setToCancel(null)}
          title={
            toCancel && toCancel.runs.length > 1
              ? `Cancel ${toCancel.runs.length} runs?`
              : "Cancel this run?"
          }
          description="An agent run's Kubernetes Job and its per-task Secret are deleted; a workflow run gets a cooperative cancel and may clean up first. A run that could not be cancelled stays active and says why."
          confirmLabel="Cancel runs"
          cancelLabel="Keep running"
          destructive
          onConfirm={async () => {
            if (!toCancel) return;
            await onCancel(toCancel.runs);
            toCancel.clear?.();
          }}
        />
      )}
    </div>
  );
}
