"use client";

import { AlertTriangleIcon, DownloadIcon, HistoryIcon, MoreHorizontalIcon } from "lucide-react";
import * as React from "react";

import type { Column } from "@/components/data-table";
import { Identifier } from "@/components/Identifier";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/use-list-state";
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
  /** The page of merged runs, newest first. */
  rows: CombinedRun[];
  newRows?: { count: number; onReveal: () => void };
  loading: boolean;
  stale: boolean;
  /** Every source failed. One failing source is `unavailable` instead. */
  error: { message: string } | null;
  onRetry: () => void;
  nextCursor: string | null;
  /** Runs matching the filters across what was fetched. */
  totalCount: number | null;
  /** True while the count covers only the newest page of each source. */
  approximateCount: boolean;
  /** Sources that failed to load, by label: the list shows the rest. */
  unavailable: string[];
  /** How much of each source the list covers, in words, while it is merged client-side. */
  coverage: string | null;
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
 * of everything that ran. Agent runs, workflow runs, deployments and job
 * runs in one list, filtered by kind, with who or what started each, when,
 * and the outcome; CSV from `⋯`. Each row links to its own detail page.
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
  approximateCount,
  unavailable,
  coverage,
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
            "Agent runs, workflow runs, deployments and job runs appear here as they start.",
        }}
        totalCount={totalCount}
        approximateCount={approximateCount}
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

      {unavailable.length > 0 && !error && (
        <p role="status" className="text-warning-fg flex min-w-0 items-start gap-1.5 text-xs">
          <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span className="min-w-0 [overflow-wrap:anywhere]">
            Not shown, could not load: {unavailable.join(", ")}.{" "}
            <button type="button" onClick={onRetry} className="underline underline-offset-2">
              Retry
            </button>
          </span>
        </p>
      )}
      {coverage && <p className="text-muted-foreground text-xs">{coverage}</p>}
    </div>
  );
}
