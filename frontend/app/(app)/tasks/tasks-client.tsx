"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  ClipboardListIcon,
  ClockIcon,
  HistoryIcon,
  Loader2Icon,
  PlayIcon,
  TerminalIcon,
} from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { RUN_TASK } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_TASK_RUNS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftTaskRun, TaskRunStatus } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import { useListControls } from "@/hooks/use-list-controls";
import type { SortState } from "@/hooks/use-list-controls";

// ── types ─────────────────────────────────────────────────────────────────

interface TaskWorkload {
  id: string;
  slug: string;
  name: string;
  kind: string;
  registeredAppSlug: string;
}

interface WorkloadResp {
  astroliftWorkloads: TaskWorkload[];
}

interface TaskRunResp {
  astroliftTaskRuns: AstroliftTaskRun[];
}

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

// ── helpers ───────────────────────────────────────────────────────────────

const SEVEN_DAYS_AGO = () => {
  const d = new Date();
  d.setDate(d.getDate() - 7);
  return d.toISOString();
};

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

const STATUS_VARIANT: Record<
  TaskRunStatus,
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "secondary",
  running: "default",
  succeeded: "outline",
  failed: "destructive",
  cancelled: "secondary",
};

// ── tab definitions ────────────────────────────────────────────────────────

type TaskTab = "templates" | "recent" | "history";
const TASK_TABS: readonly TaskTab[] = ["templates", "recent", "history"];

// ── component ─────────────────────────────────────────────────────────────

export function TasksClient() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as TaskTab | null;
  const tab: TaskTab = rawTab && TASK_TABS.includes(rawTab) ? rawTab : "templates";

  const [appFilter, setAppFilter] = React.useState<string>(
    () => searchParams.get("app") ?? ""
  );
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

  React.useEffect(() => {
    const id = setTimeout(() => updateParam("app", appFilter), 300);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appFilter]);

  // ── queries ──────────────────────────────────────────────────────────────

  const { data: workloadData, loading: workloadsLoading } = useQuery<WorkloadResp>(
    LIST_WORKLOADS,
    { variables: {}, pollInterval: 60000 }
  );

  const taskWorkloads = React.useMemo(
    () => (workloadData?.astroliftWorkloads ?? []).filter((w) => w.kind === "task"),
    [workloadData]
  );

  // Task runs — present only once the backend GQL layer is wired.
  // The query degrades gracefully: if the field doesn't exist in the
  // schema yet, the component shows EmptyState in Recent / History.
  const { data: runsData, loading: runsLoading } = useQuery<TaskRunResp>(LIST_TASK_RUNS, {
    variables: { limit: 100 },
    // Don't block render on a missing backend field — ignore GQL errors
    // so the rest of the page still renders.
    errorPolicy: "ignore",
    pollInterval: 15000,
    skip: tab === "templates",
  });

  const allRuns = React.useMemo(
    () => runsData?.astroliftTaskRuns ?? [],
    [runsData]
  );

  // "Recent" = last 7 days.
  const cutoff = SEVEN_DAYS_AGO();
  const recentRuns = React.useMemo(
    () => allRuns.filter((r) => r.createdAt >= cutoff),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [allRuns]
  );

  // History filter (app + status).
  const historyRuns = React.useMemo(() => {
    return allRuns.filter((r) => {
      if (
        appFilter &&
        !r.registeredAppSlug.toLowerCase().includes(appFilter.toLowerCase())
      )
        return false;
      if (statusFilter && r.status !== statusFilter) return false;
      return true;
    });
  }, [allRuns, appFilter, statusFilter]);

  // Filtered templates (by app).
  const filteredTemplates = React.useMemo(() => {
    if (!appFilter) return taskWorkloads;
    return taskWorkloads.filter((w) =>
      w.registeredAppSlug.toLowerCase().includes(appFilter.toLowerCase())
    );
  }, [taskWorkloads, appFilter]);

  // ── mutations ─────────────────────────────────────────────────────────────

  const [runTask, runTaskState] = useMutation<{
    runTask: MutationResultLite<AstroliftTaskRun>;
  }>(RUN_TASK, {
    refetchQueries: [{ query: LIST_TASK_RUNS, variables: { limit: 100 } }],
  });

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
          };
          const labels: Record<TaskTab, string> = {
            templates: "Templates",
            recent: "Recent",
            history: "History",
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

      {/* Filter row */}
      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder="Filter by app…"
          value={appFilter}
          onChange={(e) => setAppFilter(e.target.value)}
          className="max-w-xs"
        />
        {tab === "history" && (
          <Input
            placeholder="Filter by status…"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              updateParam("status", e.target.value);
            }}
            className="max-w-xs"
          />
        )}
      </div>

      {/* Tab content */}
      {tab === "templates" && (
        <TemplatesTab
          workloads={filteredTemplates}
          loading={workloadsLoading}
          runningWorkloadId={runningWorkloadId}
          onRunNow={handleRunNow}
          mutationLoading={runTaskState.loading}
        />
      )}
      {tab === "recent" && (
        <RunsTab
          runs={recentRuns}
          loading={runsLoading}
          emptyTitle="No recent task runs"
          emptyDescription="Task runs from the last 7 days will appear here."
          searchPlaceholder="Search recent runs…"
        />
      )}
      {tab === "history" && (
        <RunsTab
          runs={historyRuns}
          loading={runsLoading}
          emptyTitle="No task runs yet"
          emptyDescription="Once tasks are run they'll appear here. Use the Templates tab to trigger one."
          searchPlaceholder="Search history…"
        />
      )}
    </PageShell>
  );
}

// ── Templates tab ─────────────────────────────────────────────────────────

function templateSortFn(a: TaskWorkload, b: TaskWorkload, sort: SortState): number {
  const dir = sort.dir === "asc" ? 1 : -1;
  if (sort.key === "name") return a.name.localeCompare(b.name) * dir;
  if (sort.key === "app") return a.registeredAppSlug.localeCompare(b.registeredAppSlug) * dir;
  return 0;
}

interface TemplatesTabProps {
  workloads: TaskWorkload[];
  loading: boolean;
  runningWorkloadId: string | null;
  onRunNow: (w: TaskWorkload) => void;
  mutationLoading: boolean;
}

function TemplatesTab({
  workloads,
  loading,
  runningWorkloadId,
  onRunNow,
  mutationLoading,
}: TemplatesTabProps) {
  const ctrl = useListControls({
    data: workloads,
    searchFn: (w) => [w.name, w.slug, w.registeredAppSlug].join(" "),
    initialPageSize: 25,
    sortFn: templateSortFn,
  });

  if (loading && workloads.length === 0) {
    return (
      <Card>
        <CardContent className="space-y-2 p-6">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (workloads.length === 0) {
    return (
      <Card>
        <CardContent className="p-6">
          <EmptyState
            icon={<ClipboardListIcon className="size-5" />}
            title="No task workloads"
            description="Register a workload with kind=task on an app to create a named task template."
            actionHref="/apps"
            actionLabel="Go to Apps"
          />
        </CardContent>
      </Card>
    );
  }

  // Group by app from the paginated+filtered slice.
  const byApp = new Map<string, TaskWorkload[]>();
  for (const w of ctrl.rows) {
    const group = byApp.get(w.registeredAppSlug) ?? [];
    group.push(w);
    byApp.set(w.registeredAppSlug, group);
  }

  return (
    <div className="space-y-4">
      <ListControls controls={ctrl} searchPlaceholder="Search templates…" />
      {ctrl.rows.length === 0 ? (
        <Card>
          <CardContent className="p-6">
            <EmptyState
              icon={<ClipboardListIcon className="size-5" />}
              title="No matching templates"
              description="Adjust your search to find task templates."
            />
          </CardContent>
        </Card>
      ) : (
        Array.from(byApp.entries()).map(([appSlug, appWorkloads]) => (
          <Card key={appSlug}>
            <CardContent className="p-0">
              <div className="flex items-center gap-2 border-b px-4 py-3">
                <TerminalIcon className="text-muted-foreground size-4" />
                <span className="font-medium">{appSlug}</span>
                <Badge variant="secondary" className="ml-auto text-xs">
                  {appWorkloads.length} {appWorkloads.length === 1 ? "template" : "templates"}
                </Badge>
              </div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>
                      <SortableHeader sortKey="name" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                        Name
                      </SortableHeader>
                    </TableHead>
                    <TableHead>Slug</TableHead>
                    <TableHead className="w-28 text-right">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {appWorkloads.map((w) => {
                    const isRunning = runningWorkloadId === w.id;
                    return (
                      <TableRow key={w.id}>
                        <TableCell className="font-medium">{w.name}</TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          {w.slug}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            size="sm"
                            disabled={mutationLoading || isRunning}
                            onClick={() => onRunNow(w)}
                          >
                            {isRunning ? (
                              <Loader2Icon className="size-3.5 animate-spin" />
                            ) : (
                              <PlayIcon className="size-3.5" />
                            )}
                            Run now
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ))
      )}
    </div>
  );
}

// ── Runs tab (shared by Recent + History) ─────────────────────────────────

function runSortFn(a: AstroliftTaskRun, b: AstroliftTaskRun, sort: SortState): number {
  const dir = sort.dir === "asc" ? 1 : -1;
  if (sort.key === "app") return a.registeredAppSlug.localeCompare(b.registeredAppSlug) * dir;
  if (sort.key === "workload") return (a.workloadSlug ?? "").localeCompare(b.workloadSlug ?? "") * dir;
  if (sort.key === "status") return a.status.localeCompare(b.status) * dir;
  if (sort.key === "started") {
    const at = (a.startedAt ?? "").localeCompare(b.startedAt ?? "");
    return at * dir;
  }
  if (sort.key === "duration") {
    return ((a.durationSeconds ?? 0) - (b.durationSeconds ?? 0)) * dir;
  }
  return 0;
}

interface RunsTabProps {
  runs: AstroliftTaskRun[];
  loading: boolean;
  emptyTitle: string;
  emptyDescription: string;
  searchPlaceholder?: string;
}

function RunsTab({ runs, loading, emptyTitle, emptyDescription, searchPlaceholder }: RunsTabProps) {
  const ctrl = useListControls({
    data: runs,
    searchFn: (r) =>
      [r.registeredAppSlug, r.workloadSlug, r.status, r.triggeredByUsername, r.triggerKind]
        .filter(Boolean)
        .join(" "),
    initialPageSize: 25,
    initialSort: { key: "started", dir: "desc" },
    sortFn: runSortFn,
  });

  if (loading && runs.length === 0) {
    return (
      <Card>
        <CardContent className="space-y-2 p-6">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (runs.length === 0) {
    return (
      <Card>
        <CardContent className="p-6">
          <EmptyState
            icon={<HistoryIcon className="size-5" />}
            title={emptyTitle}
            description={emptyDescription}
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <ListControls
        controls={ctrl}
        searchPlaceholder={searchPlaceholder ?? "Search runs…"}
      />
      <Card>
        <CardContent className="p-0">
          {ctrl.rows.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<HistoryIcon className="size-5" />}
                title="No matching runs"
                description="Adjust your search to find task runs."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>
                    <SortableHeader sortKey="app" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                      App
                    </SortableHeader>
                  </TableHead>
                  <TableHead>
                    <SortableHeader sortKey="workload" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                      Workload
                    </SortableHeader>
                  </TableHead>
                  <TableHead>Command</TableHead>
                  <TableHead>
                    <SortableHeader sortKey="status" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                      Status
                    </SortableHeader>
                  </TableHead>
                  <TableHead>
                    <SortableHeader sortKey="duration" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                      Duration
                    </SortableHeader>
                  </TableHead>
                  <TableHead>Actor</TableHead>
                  <TableHead>
                    <SortableHeader sortKey="started" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                      Started
                    </SortableHeader>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {ctrl.rows.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">{r.registeredAppSlug}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {r.workloadSlug}
                    </TableCell>
                    <TableCell className="max-w-48 truncate font-mono text-xs">
                      {Array.isArray(r.command) ? r.command.join(" ") : r.command || "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[r.status as TaskRunStatus] ?? "secondary"}>
                        {r.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      <span className="inline-flex items-center gap-1">
                        <ClockIcon className="size-3" />
                        {formatDuration(r.durationSeconds)}
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {r.triggeredByUsername ?? r.triggerKind ?? "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {r.startedAt ? new Date(r.startedAt).toLocaleString() : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
