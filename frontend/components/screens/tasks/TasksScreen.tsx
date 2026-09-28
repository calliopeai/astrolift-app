"use client";

import {
  ClipboardListIcon,
  ClockIcon,
  HistoryIcon,
  Loader2Icon,
  PlayIcon,
  ScrollIcon,
} from "lucide-react";
import * as React from "react";

import {
  DataTable,
  type Column,
  type CursorTableController,
  type EmptyStateSpec,
} from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AstroliftTaskRun, TaskRunStatus } from "@/graphql/lifecycle/lifecycle.types";

import { formatDuration } from "@/components/screens/jobs/jobs-format";

import { TASK_TABS, type TaskTab, type TaskWorkload } from "./use-tasks";

/** Radix rejects an empty-string item value, so "no filter" needs a sentinel. */
const ANY_STATUS = "all";

const TASK_RUN_STATUSES: TaskRunStatus[] = [
  "pending",
  "running",
  "succeeded",
  "failed",
  "cancelled",
];

const STATUS_VARIANT: Record<TaskRunStatus, "default" | "secondary" | "destructive" | "outline"> = {
  pending: "secondary",
  running: "default",
  succeeded: "outline",
  failed: "destructive",
  cancelled: "secondary",
};

export interface TasksScreenProps {
  tab: TaskTab;
  setTab: (tab: TaskTab) => void;
  /**
   * Each tab's table, a container that walks its own cursor. Only the
   * active tab's slot is rendered, so hidden tabs cost nothing.
   */
  templates?: React.ReactNode;
  recent?: React.ReactNode;
  history?: React.ReactNode;
}

/**
 * Tasks: one-off container task execution. Templates to run, recent runs,
 * the filterable history and a logs gateway. Tab state lives in the URL
 * (useTasks); each table arrives as a slot.
 */
export function TasksScreen({ tab, setTab, templates, recent, history }: TasksScreenProps) {
  return (
    <PageShell
      title="Tasks"
      description="One-off container task execution — migrations, scripts, and manual interventions."
    >
      {/* Sub-navigation tabs */}
      <div
        role="tablist"
        aria-label="Task tabs"
        className="bg-muted/40 inline-flex flex-wrap rounded-md border p-1"
      >
        {TASK_TABS.map((tabKey) => {
          const active = tab === tabKey;
          const icons: Record<TaskTab, React.ReactNode> = {
            templates: <ClipboardListIcon className="size-3.5" />,
            recent: <ClockIcon className="size-3.5" />,
            history: <HistoryIcon className="size-3.5" />,
            logs: <ScrollIcon className="size-3.5" />,
          };
          const labels: Record<TaskTab, string> = {
            templates: "Templates",
            recent: "Recent",
            history: "History",
            logs: "Logs",
          };
          return (
            <button
              key={tabKey}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(tabKey)}
              className={
                "inline-flex items-center gap-1.5 rounded px-3 py-1 text-sm font-medium transition " +
                (active
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {icons[tabKey]}
              <span>{labels[tabKey]}</span>
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      {tab === "templates" && templates}
      {tab === "recent" && recent}
      {tab === "history" && history}
      {/* Gateway placeholder ported from /observe/tasks (#892). */}
      {tab === "logs" && (
        <Card>
          <CardContent className="p-6">
            <EmptyState
              icon={<ScrollIcon className="size-5" />}
              title="Task Logs"
              description="Stdout/stderr from task run containers. Stored inline for quick inspection."
            />
          </CardContent>
        </Card>
      )}
    </PageShell>
  );
}

// ── Templates tab ─────────────────────────────────────────────────────────

export interface TaskTemplatesTableProps {
  controller: CursorTableController<TaskWorkload>;
  runningWorkloadId: string | null;
  onRunNow: (w: TaskWorkload) => void;
  mutationLoading: boolean;
}

export function TaskTemplatesTable({
  controller,
  runningWorkloadId,
  onRunNow,
  mutationLoading,
}: TaskTemplatesTableProps) {
  const columns: Column<TaskWorkload>[] = [
    {
      id: "app",
      header: "App",
      cell: (w) => <span className="font-medium">{w.registeredAppSlug}</span>,
    },
    {
      id: "name",
      header: "Name",
      cell: (w) => <span className="font-medium">{w.name}</span>,
    },
    {
      id: "slug",
      header: "Slug",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (w) => w.slug,
    },
    {
      id: "action",
      header: "Action",
      align: "right",
      width: "w-28",
      cell: (w) => {
        const isRunning = runningWorkloadId === w.id;
        return (
          <Button size="sm" disabled={mutationLoading || isRunning} onClick={() => onRunNow(w)}>
            {isRunning ? (
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
    <DataTable
      label="Task templates"
      controller={controller}
      columns={columns}
      getRowId={(w) => w.id}
      empty={{
        icon: <ClipboardListIcon className="size-5" />,
        title: "No task workloads",
        description:
          "Register a workload with kind=task on an app to create a named task template.",
        actionHref: "/apps",
        actionLabel: "Go to Apps",
      }}
    />
  );
}

// ── Runs tab (shared by Recent + History) ─────────────────────────────────

export interface TaskRunsTableProps {
  controller: CursorTableController<AstroliftTaskRun>;
  /** Recent is newest-first with no filter; History adds the status filter. */
  kind: "recent" | "history";
  /** Server-side status filter. Only History exposes the control. */
  status?: string;
  onStatusChange?: (next: string) => void;
}

export function TaskRunsTable({ controller, kind, status, onStatusChange }: TaskRunsTableProps) {
  const statusFilter = status ?? "";
  const empty: EmptyStateSpec =
    kind === "recent"
      ? {
          icon: <HistoryIcon className="size-5" />,
          title: "No recent task runs",
          description: "Task runs appear here as soon as one is triggered, newest first.",
        }
      : // The status filter is a server argument, not a search term, so
        // DataTable reports its result as `empty` rather than
        // `emptyFiltered` — the copy has to name the filter itself.
        {
          icon: <HistoryIcon className="size-5" />,
          title: statusFilter ? `No ${statusFilter} task runs` : "No task runs yet",
          description: statusFilter
            ? "No task run has that status. Clear the status filter to see the whole history."
            : "Once tasks are run they'll appear here. Use the Templates tab to trigger one.",
        };

  const columns: Column<AstroliftTaskRun>[] = [
    {
      id: "app",
      header: "App",
      cellClassName: "font-medium",
      cell: (r) => r.registeredAppSlug,
    },
    {
      id: "workload",
      header: "Workload",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (r) => r.workloadSlug,
    },
    {
      id: "command",
      header: "Command",
      cellClassName: "max-w-48 truncate font-mono text-xs",
      cell: (r) => (Array.isArray(r.command) ? r.command.join(" ") : r.command || "—"),
    },
    {
      id: "status",
      header: "Status",
      cell: (r) => (
        <Badge variant={STATUS_VARIANT[r.status as TaskRunStatus] ?? "secondary"}>{r.status}</Badge>
      ),
    },
    {
      id: "duration",
      header: "Duration",
      cellClassName: "font-mono text-xs",
      cell: (r) => (
        <span className="inline-flex items-center gap-1">
          <ClockIcon className="size-3" />
          {formatDuration(r.durationSeconds)}
        </span>
      ),
    },
    {
      id: "actor",
      header: "Actor",
      cellClassName: "text-muted-foreground text-sm",
      cell: (r) => r.triggeredByUsername ?? r.triggerKind ?? "—",
    },
    {
      id: "started",
      header: "Started",
      cellClassName: "text-muted-foreground text-sm",
      cell: (r) => (r.startedAt ? new Date(r.startedAt).toLocaleString() : "—"),
    },
  ];

  return (
    <DataTable
      label="Task runs"
      controller={controller}
      columns={columns}
      getRowId={(r) => r.id}
      rowHref={(r) => `/tasks/runs/${r.id}`}
      searchPlaceholder={kind === "recent" ? "Search recent runs…" : "Search history…"}
      toolbar={
        onStatusChange ? (
          <Select
            value={status ? status : ANY_STATUS}
            onValueChange={(v) => onStatusChange(v === ANY_STATUS ? "" : v)}
          >
            <SelectTrigger size="sm" className="w-40" aria-label="Filter by status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY_STATUS}>All statuses</SelectItem>
              {TASK_RUN_STATUSES.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : undefined
      }
      empty={empty}
      emptyFiltered={{
        title: "No matching runs",
        description:
          "No task run matches that search under the current status filter. The server looks at the app, workload, status, the batch/v1 Job name, and the operator who triggered it.",
      }}
    />
  );
}
