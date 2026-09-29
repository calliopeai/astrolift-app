"use client";

import { DownloadIcon, HistoryIcon, MoreHorizontalIcon } from "lucide-react";
import * as React from "react";

import type { Column } from "@/components/data-table";
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

import { type CombinedRun, RUN_KIND_LABEL, type RunOutcome } from "./combined-runs";
import { adminCrumbs } from "./header";

export interface CombinedRunsScreenProps {
  /** List state: views, search, chips, cursor (see combined-runs.ts). */
  list: ListStateController;
  /** One page of runs of every kind, newest first unless sorted oldest first. */
  rows: CombinedRun[];
  newRows?: { count: number; onReveal: () => void };
  loading: boolean;
  stale: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  nextCursor: string | null;
  /** Runs matching the filters, every kind, across all pages (the server's exact count). */
  totalCount: number | null;
  /** Every run the filters match, not only the page on screen. */
  onExportCsv: () => void;
}

const DOT: Record<RunOutcome, "ok" | "warn" | "error" | "muted" | "pending"> = {
  running: "pending",
  waiting: "warn",
  succeeded: "ok",
  failed: "error",
  cancelled: "muted",
  unknown: "muted",
};

function took(s: number | null): string {
  if (s === null) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

/**
 * Admin › Usage & governance › Runs (spec 44 §4.4, decision 14): the audit
 * of everything that ran. Agent runs, workflow runs, deployments, job runs
 * and task runs in one list, filtered by kind, with who or what started
 * each, when, and the outcome; CSV from `⋯`. Each row links to its own
 * detail page. Pure; the data half is useCombinedRuns.
 */
export function CombinedRunsScreen({
  list,
  rows,
  newRows,
  loading,
  stale,
  error,
  onRetry,
  nextCursor,
  totalCount,
  onExportCsv,
}: CombinedRunsScreenProps) {
  const fmt = useFormatters();

  const columns: Column<CombinedRun>[] = [
    {
      id: "run",
      header: "Run",
      cellClassName: "max-w-48",
      cell: (r) => (
        <div className="flex min-w-0 flex-col">
          <span className="text-muted-foreground text-xs">{RUN_KIND_LABEL[r.kind]}</span>
          <Identifier value={r.id} kind="id" copyable={false} className="text-xs" />
        </div>
      ),
    },
    {
      id: "subject",
      header: "What",
      cellClassName: "max-w-72",
      cell: (r) => (
        <div className="flex min-w-0 flex-col">
          <span className="truncate font-mono text-sm" title={r.subject}>
            {r.subject || "—"}
          </span>
          {r.scope && (
            <span className="text-muted-foreground truncate font-mono text-xs" title={r.scope}>
              {r.scope}
            </span>
          )}
        </div>
      ),
    },
    {
      id: "startedBy",
      header: "Started by",
      cellClassName: "max-w-64",
      cell: (r) => (
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-sm" title={r.startedBy || undefined}>
            {r.startedBy || <span className="text-muted-foreground">not recorded</span>}
          </span>
          {r.trigger && (
            <span className="text-muted-foreground truncate font-mono text-xs" title={r.trigger}>
              {r.trigger}
            </span>
          )}
        </div>
      ),
    },
    {
      id: "at",
      header: "When",
      sortKey: "at",
      cellClassName: "font-mono text-xs whitespace-nowrap",
      cell: (r) => (r.at ? fmt.formatDateTime(r.at) : "—"),
    },
    {
      id: "took",
      header: "Took",
      align: "right",
      cellClassName: "font-mono text-xs tabular-nums",
      cell: (r) => took(r.durationSeconds),
    },
    {
      id: "outcome",
      header: "Outcome",
      cell: (r) => (
        <span className="inline-flex min-w-0 items-center gap-1.5 font-mono text-xs">
          <StatusDot status={DOT[r.outcome]} />
          <span>{r.outcome}</span>
          {r.status && r.status !== r.outcome && (
            <span className="text-muted-foreground truncate" title={r.status}>
              · {r.status}
            </span>
          )}
        </span>
      ),
    },
  ];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3 p-6">
      <ListPage<CombinedRun>
        header={{ crumbs: adminCrumbs("run-audit", "Runs"), title: "Runs" }}
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
          description:
            "Agent runs, workflow runs, deployments, job runs and task runs appear here as they start.",
        }}
        totalCount={totalCount}
        nextCursor={nextCursor}
        newRows={newRows}
        menu={
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="More">
                <MoreHorizontalIcon className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={onExportCsv} disabled={loading || rows.length === 0}>
                <DownloadIcon className="size-4" />
                Export CSV
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        }
      />
    </div>
  );
}
