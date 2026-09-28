"use client";

import { useMutation } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import {
  useCursorTable,
  type CursorPage,
  type CursorTableController,
} from "@/components/data-table";
import { RUN_TASK } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_TASK_RUNS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftTaskRun } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_WORKLOADS_PAGE } from "@/graphql/registry/registry.queries";

// ── types ─────────────────────────────────────────────────────────────────

export interface TaskWorkload {
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

/**
 * `astroliftWorkloadsPage` has no `kind:` argument, so the one axis that
 * *defines* this tab has to ride on `search` — an OR of `icontains` over
 * the workload's name, slug, kind and the owning app's slug, which makes
 * "task" a superset of the task templates. The row guard in
 * `useTaskTemplates` drops whatever matched on the wrong column, so a
 * "Run now" button can never land on a deployment. The trade is the tab's
 * own search box: `search` is already carrying the kind filter.
 */
const TASK_KIND_TERM = "task";

// "logs" is a gateway placeholder folded in from /observe/tasks (#892).
export type TaskTab = "templates" | "recent" | "history" | "logs";
export const TASK_TABS: readonly TaskTab[] = ["templates", "recent", "history", "logs"];

/**
 * The page half of TasksScreen: the URL-backed tab and status filter, and
 * the run-task mutation the Templates tab fires.
 */
export function useTasks() {
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

  function onStatusChange(next: string) {
    setStatusFilter(next);
    updateParam("status", next);
  }

  // No `refetchQueries`: a successful run switches to the Recent tab, which
  // mounts that walk fresh (`cache-and-network`). Naming the page query here
  // instead would target a query that is not mounted while the operator is
  // on Templates.
  const [runTask, runTaskState] = useMutation<{
    runTask: MutationResultLite<AstroliftTaskRun>;
  }>(RUN_TASK);

  const [runningWorkloadId, setRunningWorkloadId] = React.useState<string | null>(null);

  async function onRunNow(workload: TaskWorkload) {
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

  return {
    tab,
    setTab,
    statusFilter,
    onStatusChange,
    runningWorkloadId,
    onRunNow,
    mutationLoading: runTaskState.loading,
  };
}

/** Task templates (workloads of kind=task). Mounted only on the Templates tab. */
export function useTaskTemplates() {
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

  return { controller };
}

/**
 * Task runs for the Recent and History tabs. `urlKey` prefixes the tab's
 * search + page size in the URL (`?recent-q=`); `status` is the server-side
 * status filter only History exposes.
 */
export function useTaskRuns(urlKey: string, status?: string) {
  // `astroliftTaskRunsPage` takes `appSlug`, `workloadSlug`, `status`,
  // `search`, `limit` and `after`. No sort argument, so no column declares
  // a `sortKey`; the walk is newest-first on `(-created_at, -guid)`.
  const controller = useCursorTable<AstroliftTaskRun>({
    query: LIST_TASK_RUNS_PAGE,
    variables: { status: status || null },
    extract: (d) => (d as TaskRunsPageResp | undefined)?.astroliftTaskRunsPage,
    searchVariable: "search",
    urlKey,
    pollInterval: 15000,
  });
  return { controller };
}
