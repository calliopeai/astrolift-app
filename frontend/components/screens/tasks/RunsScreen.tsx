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
import { useTranslations } from "next-intl";

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

import { type RunOutcome, type RunRow } from "./runs-list";

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
  const t = useTranslations("taskRuns");
  const fmt = useFormatters();
  const [toCancel, setToCancel] = React.useState<{ runs: RunRow[]; clear?: () => void } | null>(
    null
  );
  const [toRetry, setToRetry] = React.useState<{ runs: RunRow[]; clear?: () => void } | null>(null);

  const columns: Column<RunRow>[] = [
    {
      id: "run",
      header: t("run"),
      cellClassName: "max-w-48",
      cell: (r) =>
        r.upcoming ? (
          <div className="flex min-w-0 flex-col">
            <span className="truncate font-mono text-xs" title={r.id}>
              {r.id}
            </span>
            <span className="text-muted-foreground text-xs">{t("scheduledRun")}</span>
          </div>
        ) : (
          <div className="flex min-w-0 flex-col">
            <Identifier value={r.id} kind="id" copyable={false} className="text-xs" />
            <span className="text-muted-foreground text-xs">{t(`kinds.${r.kind}`)}</span>
          </div>
        ),
    },
    ...(embedded
      ? []
      : [
          {
            id: "subject",
            header: t("what"),
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
      header: t("status"),
      cell: (r) => <RunOutcomeCell run={r} />,
    },
    {
      id: "took",
      header: t("took"),
      align: "right",
      cellClassName: "font-mono text-xs tabular-nums",
      cell: (r) => formatTook(r.durationSeconds),
    },
    {
      id: "trigger",
      header: t("trigger"),
      cellClassName: "max-w-48",
      cell: (r) => (
        <div className="flex min-w-0 flex-col">
          <span className="truncate font-mono text-xs" title={r.trigger || undefined}>
            {r.trigger || <span className="text-muted-foreground">{t("notRecorded")}</span>}
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
      header: upcomingView(list) ? t("fires") : t("started"),
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
                  {t("retryCount", { count: retryable.length })}
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
                  {t("cancelCount", { count: cancellable.length })}
                </Button>
              )}
            </>
          );
        }
      : undefined;

  const rowActions = (r: RunRow) => (
    <>
      <DropdownMenuItem asChild>
        <Link href={r.href}>{r.upcoming ? t("openAgent") : t("openRun")}</Link>
      </DropdownMenuItem>
      {r.watchHref && (
        <DropdownMenuItem asChild>
          <Link href={r.watchHref} target="_blank" rel="noreferrer">
            <MonitorPlayIcon className="size-4" />
            {t("watchLive")}
            <ExternalLinkIcon className="size-3.5" />
          </Link>
        </DropdownMenuItem>
      )}
      {onRetryRuns && r.retry && (
        <DropdownMenuItem onSelect={() => setToRetry({ runs: [r] })}>
          <RotateCcwIcon className="size-4" />
          {t("retryRun")}
        </DropdownMenuItem>
      )}
      {onCancel && r.cancel && (
        <DropdownMenuItem onSelect={() => setToCancel({ runs: [r] })}>
          <XCircleIcon className="size-4" />
          {t("cancelRun")}
        </DropdownMenuItem>
      )}
    </>
  );

  const body = {
    list,
    label: t("title"),
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
          title: t("emptyTitle"),
          description: t("emptyEmbedded"),
        }
      : {
          icon: <HistoryIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
          actionHref: "/agents?tab=dispatch",
          actionLabel: t("runAnAgent"),
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
            crumbs: [areaSwitcher(NAV, "agents", "runs"), { label: t("title") }],
            title: t("title"),
            primaryAction: (
              <Button asChild size="sm">
                <Link href="/agents?tab=dispatch">
                  <PlayIcon className="size-4" />
                  {t("runAgent")}
                </Link>
              </Button>
            ),
            menu: (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" aria-label={t("more")}>
                    <MoreHorizontalIcon className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem asChild>
                    <Link href="/tasks/templates">
                      <ClipboardListIcon className="size-4" />
                      {t("taskTemplates")}
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
              ? t("retryManyTitle", { count: toRetry.runs.length })
              : t("retryOneTitle")
          }
          description={t("retryDescription")}
          confirmLabel={t("retryConfirm")}
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
              ? t("cancelManyTitle", { count: toCancel.runs.length })
              : t("cancelOneTitle")
          }
          description={t("cancelDescription")}
          confirmLabel={t("cancelConfirm")}
          cancelLabel={t("keepRunning")}
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
