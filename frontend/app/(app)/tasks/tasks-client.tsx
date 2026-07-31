"use client";

import { useMutation } from "@apollo/client/react";
import {
  ClipboardListIcon,
  ClockIcon,
  HistoryIcon,
  Loader2Icon,
  PlayIcon,
  ScrollIcon,
} from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import {
  DataTable,
  useCursorTable,
  type Column,
  type CursorPage,
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
import { RUN_TASK } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_TASK_RUNS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftTaskRun, TaskRunStatus } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_WORKLOADS_PAGE } from "@/graphql/registry/registry.queries";

// ── types ─────────────────────────────────────────────────────────────────

interface TaskWorkload {
  id: string;
  slug: string;
  name: string;
  kind: string;
  registeredAppSlug: string;
}

interface WorkloadsPageResp {
  astroliftWorkloadsPage: CursorPage<TaskWorkload>;
}

interface TaskRunsPageResp {
  astroliftTaskRunsPage: CursorPage<AstroliftTaskRun>;
}

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

// ── helpers ───────────────────────────────────────────────────────────────

/**
 * `astroliftWorkloadsPage` has no `kind:` argument, so the one axis that
 * *defines* this tab has to ride on `search` — an OR of `icontains` over
 * the workload's name, slug, kind and the owning app's slug, which makes
 * "task" a superset of the task templates. The row guard in `TemplatesTab`
 * drops whatever matched on the wrong column, so a "Run now" button can
 * never land on a deployment. The trade is the tab's own search box:
 * `search` is already carrying the kind filter.
 */
const TASK_KIND_TERM = "task";

/** Radix rejects an empty-string item value, so "no filter" needs a sentinel. */
const ANY_STATUS = "all";

const TASK_RUN_STATUSES: TaskRunStatus[] = [
  "pending",
  "running",
  "succeeded",
  "failed",
  "cancelled",
];

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

const STATUS_VARIANT: Record<TaskRunStatus, "default" | "secondary" | "destructive" | "outline"> = {
  pending: "secondary",
  running: "default",
  succeeded: "outline",
  failed: "destructive",
  cancelled: "secondary",
};

// ── tab definitions ────────────────────────────────────────────────────────

// "logs" is a gateway placeholder folded in from /observe/tasks (#892).
type TaskTab = "templates" | "recent" | "history" | "logs";
const TASK_TABS: readonly TaskTab[] = ["templates", "recent", "history", "logs"];

// ── component ─────────────────────────────────────────────────────────────

export function TasksClient() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as TaskTab | null;
  const tab: TaskTab = rawTab && TASK_TABS.includes(rawTab) ? rawTab : "templates";

  // Status is a server-side filter argument on `astroliftTaskRunsPage`, so
  // it changes the query rather than the rows in hand; the cursor walk
  // restarts at page one when it flips.
  const [statusFilter, setStatusFilter] = React.useState<string>(
    () => searchParams.get("status") ?? ""
  );

  function updateParam(key: string, value: string, defaultVal = "") {
    const params = new URLSearchParams(searchParams.toString());
    if (value && value !== defaultVal) {
      params.set(key, value);
    } else {
      params.delete(key);
    }
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  }

  // "templates" is the default — omit it from the URL to keep /tasks clean.
  const setTab = (v: TaskTab) => updateParam("tab", v, "templates");

  function handleStatusChange(next: string) {
    setStatusFilter(next);
    updateParam("status", next);
  }

  // ── mutations ─────────────────────────────────────────────────────────────

  // No `refetchQueries`: a successful run switches to the Recent tab, which
  // mounts that walk fresh (`cache-and-network`). Naming the page query here
  // instead would target a query that is not mounted while the operator is
  // on Templates.
  const [runTask, runTaskState] = useMutation<{
    runTask: MutationResultLite<AstroliftTaskRun>;
  }>(RUN_TASK);

  const [runningWorkloadId, setRunningWorkloadId] = React.useState<string | null>(null);

  async function handleRunNow(workload: TaskWorkload) {
    setRunningWorkloadId(workload.id);
    try {
      const { data } = await runTask({
        variables: {
          input: {
            workloadId: workload.id,
          },
        },
      });
      const result = data?.runTask;
      if (result?.ok) {
        toast.success(`Task started for ${workload.name}`);
        setTab("recent");
      } else {
        toast.error(result?.errors[0]?.message ?? "Failed to start task");
      }
    } catch {
      toast.error("Failed to start task");
    } finally {
      setRunningWorkloadId(null);
    }
  }

  // ── render ────────────────────────────────────────────────────────────────

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
      {tab === "templates" && (
        <TemplatesTab
          runningWorkloadId={runningWorkloadId}
          onRunNow={handleRunNow}
          mutationLoading={runTaskState.loading}
        />
      )}
      {tab === "recent" && (
        <RunsTab
          urlKey="recent"
          searchPlaceholder="Search recent runs…"
          empty={{
            icon: <HistoryIcon className="size-5" />,
            title: "No recent task runs",
            description: "Task runs appear here as soon as one is triggered, newest first.",
          }}
        />
      )}
      {tab === "history" && (
        <RunsTab
          urlKey="hist"
          searchPlaceholder="Search history…"
          status={statusFilter}
          onStatusChange={handleStatusChange}
          // The status filter is a server argument, not a search term, so
          // DataTable reports its result as `empty` rather than
          // `emptyFiltered` — the copy has to name the filter itself.
          empty={{
            icon: <HistoryIcon className="size-5" />,
            title: statusFilter ? `No ${statusFilter} task runs` : "No task runs yet",
            description: statusFilter
              ? "No task run has that status. Clear the status filter to see the whole history."
              : "Once tasks are run they'll appear here. Use the Templates tab to trigger one.",
          }}
        />
      )}
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

interface TemplatesTabProps {
  runningWorkloadId: string | null;
  onRunNow: (w: TaskWorkload) => void;
  mutationLoading: boolean;
}

function TemplatesTab({ runningWorkloadId, onRunNow, mutationLoading }: TemplatesTabProps) {
  // `astroliftWorkloadsPage` takes `appSlug`, `search`, `limit` and `after`
  // — no sort argument, so no column declares a `sortKey`. Rows arrive
  // newest-first from the server's `(-created_at, -guid)` seek key.
  const table = useCursorTable<TaskWorkload>({
    query: LIST_WORKLOADS_PAGE,
    variables: { search: TASK_KIND_TERM },
    extract: (d) => (d as WorkloadsPageResp | undefined)?.astroliftWorkloadsPage,
    urlKey: "tpl",
    pollInterval: 60000,
  });

  const taskRows = React.useMemo(() => table.rows.filter((w) => w.kind === "task"), [table.rows]);

  // Same controller, guarded rows: the walk — cursor, page size, error and
  // retry — still comes from the server. `state` has to be recomputed or a
  // page whose rows all fail the guard renders as nothing at all.
  const controller: CursorTableController<TaskWorkload> = {
    ...table,
    rows: taskRows,
    state: table.state === "ready" && taskRows.length === 0 ? "empty" : table.state,
  };

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

interface RunsTabProps {
  /** URL prefix for this tab's search + page size (`?recent-q=`). */
  urlKey: string;
  empty: EmptyStateSpec;
  searchPlaceholder?: string;
  /** Server-side status filter. Only History exposes the control. */
  status?: string;
  onStatusChange?: (next: string) => void;
}

function RunsTab({ urlKey, empty, searchPlaceholder, status, onStatusChange }: RunsTabProps) {
  // `astroliftTaskRunsPage` takes `appSlug`, `workloadSlug`, `status`,
  // `search`, `limit` and `after`. No sort argument, so no column declares
  // a `sortKey`; the walk is newest-first on `(-created_at, -guid)`.
  const table = useCursorTable<AstroliftTaskRun>({
    query: LIST_TASK_RUNS_PAGE,
    variables: { status: status || null },
    extract: (d) => (d as TaskRunsPageResp | undefined)?.astroliftTaskRunsPage,
    searchVariable: "search",
    urlKey,
    pollInterval: 15000,
  });

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
      controller={table}
      columns={columns}
      getRowId={(r) => r.id}
      rowHref={(r) => `/tasks/runs/${r.id}`}
      searchPlaceholder={searchPlaceholder}
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
