"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { type ListDefinition, standardViews, useListState } from "@/components/list/use-list-state";
import { RUN_TASK } from "@/graphql/lifecycle/lifecycle.mutations";
import type { AstroliftTaskRun } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_WORKLOADS_PAGE } from "@/graphql/registry/registry.queries";

import type { TaskTemplatesScreenProps } from "./TaskTemplatesScreen";

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

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

/**
 * `astroliftWorkloadsPage` has no `kind:` argument, so the kind rides on
 * `search`, an OR of `icontains` over the workload's name, slug, kind and
 * the owning app's slug: "task" is a superset of the task templates. A
 * typed search replaces it (the person is looking for something), and the
 * row guard below drops whatever is not kind=task either way, so Run now
 * can never land on a deployment.
 */
const TASK_KIND_TERM = "task";

export const TASK_TEMPLATES_LIST: ListDefinition = {
  id: "tasks.templates",
  fields: [{ key: "app", label: "App" }],
  searchPlaceholder: "Search task templates…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews({ owner: "me" }, [], {
    mineNote: "Task templates don't record an owner yet, so Mine stays empty until they do.",
  }),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

/**
 * Task templates (workloads of kind=task) and Run now. A started run is
 * shown on the Runs list, narrowed to task runs.
 */
export function useTaskTemplates(): TaskTemplatesScreenProps {
  const router = useRouter();
  const list = useListState(TASK_TEMPLATES_LIST);
  const mine = list.filters.owner === "me";

  const { data, loading, error, refetch } = useQuery<WorkloadsPageResp>(LIST_WORKLOADS_PAGE, {
    variables: {
      appSlug: list.filters.app || null,
      search: list.state.q.trim() || TASK_KIND_TERM,
      limit: list.state.pageSize,
      after: list.state.after,
    },
    skip: mine,
    fetchPolicy: "cache-and-network",
    pollInterval: 60_000,
  });

  const page = data?.astroliftWorkloadsPage;
  const rows = React.useMemo(
    () => (mine ? [] : (page?.items ?? []).filter((w) => w.kind === "task")),
    [mine, page?.items]
  );

  const [runTask, runTaskState] = useMutation<{
    runTask: MutationResultLite<AstroliftTaskRun>;
  }>(RUN_TASK);
  const [runningWorkloadId, setRunningWorkloadId] = React.useState<string | null>(null);

  async function onRunNow(workload: TaskWorkload) {
    setRunningWorkloadId(workload.id);
    try {
      const { data: res } = await runTask({ variables: { input: { workloadId: workload.id } } });
      const result = res?.runTask;
      if (result?.ok) {
        toast.success(`Task started for ${workload.name}`);
        router.push("/tasks?kind=task");
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
    list,
    rows,
    loading: loading && !data && !mine,
    stale: loading && Boolean(data),
    error: error && !data ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
    nextCursor: mine ? null : (page?.nextCursor ?? null),
    runningWorkloadId,
    mutationLoading: runTaskState.loading,
    onRunNow,
  };
}
