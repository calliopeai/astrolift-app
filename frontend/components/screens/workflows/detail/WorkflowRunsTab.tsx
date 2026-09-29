"use client";

import { HistoryIcon } from "lucide-react";

import type { Column } from "@/components/data-table";
import { Identifier } from "@/components/Identifier";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/use-list-state";
import type { RunRow } from "@/components/screens/tasks/runs-list";
import { formatTook, RunOutcomeCell } from "@/components/screens/tasks/RunsScreen";
import { WorkflowView } from "@/components/viz";
import type { WorkflowSnapshot } from "@/components/viz/core/workflow-model";
import { useFormatters } from "@/lib/i18n/formatters";

export interface WorkflowRunsTabProps {
  /** List state in the URL: views, search, chips, cursor (WORKFLOW_RUNS_LIST). */
  list: ListStateController;
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
  /** How much of the source the list covers while it is filtered in the browser. */
  coverage: string | null;
  /** The workflow's line with its live runs placed on it; null when none is placed. */
  live: WorkflowSnapshot | null;
}

/**
 * A workflow's Runs tab (spec 44 §5.1, §5.2): the shared Runs list,
 * embedded and narrowed to this workflow, live with new rows behind the
 * pill and paged by cursor; each row opens the run's own page. While runs
 * are live and the engine has placed them, the workflow view of the line
 * sits above the list, in the person's chosen style with its legend. Pure.
 */
export function WorkflowRunsTab({
  list,
  rows,
  newRows,
  loading,
  stale,
  error,
  onRetry,
  nextCursor,
  totalCount,
  approximateCount,
  coverage,
  live,
}: WorkflowRunsTabProps) {
  const fmt = useFormatters();

  const columns: Column<RunRow>[] = [
    {
      id: "run",
      header: "Run",
      cellClassName: "max-w-48",
      cell: (r) => <Identifier value={r.id} kind="id" copyable={false} className="text-xs" />,
    },
    { id: "status", header: "Status", cell: (r) => <RunOutcomeCell run={r} /> },
    {
      id: "took",
      header: "Took",
      align: "right",
      sortKey: "took",
      cellClassName: "font-mono text-xs tabular-nums",
      cell: (r) => formatTook(r.durationSeconds),
    },
    {
      id: "trigger",
      header: "Trigger",
      cellClassName: "max-w-48",
      cell: (r) => (
        <span className="block truncate font-mono text-xs" title={r.trigger || undefined}>
          {r.trigger || <span className="text-muted-foreground">not recorded</span>}
        </span>
      ),
    },
    {
      id: "at",
      header: "Started",
      sortKey: "at",
      cellClassName: "font-mono text-xs whitespace-nowrap",
      cell: (r) => (r.at ? fmt.formatDateTime(r.at) : "—"),
    },
  ];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      {live && (
        <WorkflowView
          snapshot={live}
          title="Live"
          description={`${live.trains.length} ${live.trains.length === 1 ? "run" : "runs"} in flight, placed where the engine reports them`}
        />
      )}
      <ListPage<RunRow>
        embedded
        list={list}
        label="Runs"
        columns={columns}
        rows={rows}
        getRowId={(r) => r.key}
        rowHref={(r) => r.href}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <HistoryIcon className="size-5" />,
          title: "No runs yet",
          description: "This workflow hasn't run yet. Run starts one; it appears here live.",
        }}
        totalCount={totalCount}
        approximateCount={approximateCount}
        nextCursor={nextCursor}
        newRows={newRows}
      />
      {coverage && <p className="text-muted-foreground text-xs">{coverage}</p>}
    </div>
  );
}
