"use client";

import { ClipboardListIcon, Loader2Icon, PlayIcon } from "lucide-react";
import * as React from "react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/use-list-state";
import { Button } from "@/components/ui/button";
import { areaSwitcher, NAV } from "@/lib/shell/nav-model";

import type { TaskWorkload } from "./use-tasks";

export interface TaskTemplatesScreenProps {
  list: ListStateController;
  /** The page of task templates (workloads of kind=task). */
  rows: TaskWorkload[];
  loading: boolean;
  stale: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  nextCursor: string | null;
  /** The template whose run is being started. */
  runningWorkloadId: string | null;
  mutationLoading: boolean;
  onRunNow: (workload: TaskWorkload) => void;
}

/**
 * Task templates: the one-off container tasks an app registers (migrations,
 * scripts, manual interventions), each with Run now. Their runs are on the
 * Runs list; this is its own route so Runs stays one list (Leo's rule 3).
 * Pure.
 */
export function TaskTemplatesScreen({
  list,
  rows,
  loading,
  stale,
  error,
  onRetry,
  nextCursor,
  runningWorkloadId,
  mutationLoading,
  onRunNow,
}: TaskTemplatesScreenProps) {
  const columns: Column<TaskWorkload>[] = [
    {
      id: "name",
      header: "Name",
      cellClassName: "max-w-72",
      cell: (w) => (
        <span className="block truncate font-medium" title={w.name}>
          {w.name}
        </span>
      ),
    },
    {
      id: "app",
      header: "App",
      cellClassName: "max-w-56 font-mono text-xs",
      cell: (w) => (
        <span className="block truncate" title={w.registeredAppSlug}>
          {w.registeredAppSlug}
        </span>
      ),
    },
    {
      id: "slug",
      header: "Slug",
      cellClassName: "text-muted-foreground max-w-56 font-mono text-xs",
      cell: (w) => (
        <span className="block truncate" title={w.slug}>
          {w.slug}
        </span>
      ),
    },
    {
      id: "action",
      header: <span className="sr-only">Run</span>,
      label: "Run",
      align: "right",
      width: "w-28",
      // Above the row's stretched link, so the button runs instead of opening the row.
      cellClassName: "relative z-10",
      cell: (w) => {
        const starting = runningWorkloadId === w.id;
        return (
          <Button size="sm" disabled={mutationLoading || starting} onClick={() => onRunNow(w)}>
            {starting ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <PlayIcon className="size-3.5" />
            )}
            Run now
          </Button>
        );
      },
    },
  ];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3">
      <ListPage<TaskWorkload>
        header={{
          crumbs: [
            areaSwitcher(NAV, "agents", "runs"),
            { label: "Runs", href: "/tasks" },
            { label: "Task templates" },
          ],
          title: "Task templates",
          context: "One-off container tasks: migrations, scripts and manual interventions.",
        }}
        list={list}
        label="Task templates"
        columns={columns}
        rows={rows}
        getRowId={(w) => w.id}
        rowHref={(w) =>
          `/apps/${encodeURIComponent(w.registeredAppSlug)}/workloads/${encodeURIComponent(w.slug)}`
        }
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        empty={{
          icon: <ClipboardListIcon className="size-5" />,
          title: "No task workloads",
          description:
            "Register a workload with kind=task on an app to create a named task template.",
          actionHref: "/apps",
          actionLabel: "Go to Apps",
        }}
        nextCursor={nextCursor}
      />
    </div>
  );
}
