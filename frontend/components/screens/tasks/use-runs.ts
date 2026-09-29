"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { useHeldRows } from "@/components/list/use-held-rows";
import { useListState } from "@/components/list/use-list-state";
import { CANCEL_TASK } from "@/graphql/agents/agents.mutations";
import { LIST_AGENT_TASKS_PAGE } from "@/graphql/agents/agents.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { LIST_TASK_RUNS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftTaskRun } from "@/graphql/lifecycle/lifecycle.types";
import { useMe, useModules } from "@/graphql/user/user.hooks";
import { useCancelWorkflowInstance } from "@/graphql/workflows/workflows.hooks";
import { LIST_WORKFLOW_DEFINITION_RUNS } from "@/graphql/workflows/tiered.queries";
import type { WorkflowDefinitionRun } from "@/graphql/workflows/tiered.types";

import {
  activeSources,
  AGENT_RUNS_LIST,
  type AgentTaskSource,
  filterRunRows,
  fromAgentTask,
  fromTaskRun,
  fromWorkflowRun,
  pageRunRows,
  type RunKind,
  type RunRow,
  RUNS_LIST,
  sortRunRows,
} from "./runs-list";
import type { RunsScreenProps } from "./RunsScreen";

/**
 * INTERIM, CLIENT-SIDE MERGE. There is no runs query across kinds, so this
 * reads the newest SOURCE_LIMIT of each source (agent tasks, workflow runs,
 * container task runs), maps them to one row shape, and filters, sorts and
 * pages them in the browser. The screen says so under the list when a
 * source had more. Replace with one paged `runs(filter, search, sort,
 * first, after)` query (see the report's needsBackend) and drop the merge.
 */
const SOURCE_LIMIT = 100;
const POLL_MS = 10_000;

interface AgentTasksPageResp {
  agentTasksPage: {
    items: AgentTaskSource[];
    nextCursor: string | null;
    totalCount: number | null;
  };
}
interface WorkflowRunsResp {
  workflowDefinitionRuns: WorkflowDefinitionRun[];
}
interface TaskRunsPageResp {
  astroliftTaskRunsPage: { items: AstroliftTaskRun[]; nextCursor: string | null };
}
interface CancelTaskResp {
  cancelTask: { ok: boolean; errors: { message: string }[] };
}

const SOURCE_LABEL: Record<RunKind, string> = {
  agent: "agent runs",
  workflow: "workflow runs",
  task: "task runs",
};

export interface UseRunsOptions {
  /** An agent's Runs tab: only this agent's runs (its Workload id scopes the query). */
  agent?: { id: string; slug: string };
}

/** The Runs list's data half, for the Runs page or an agent's Runs tab. */
export function useRuns({ agent }: UseRunsOptions = {}): RunsScreenProps {
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const modules = useModules();
  const { user } = useMe();
  const me = user?.profile?.username ?? null;
  const list = useListState(agent ? AGENT_RUNS_LIST : RUNS_LIST);
  const [now] = React.useState(() => Date.now());

  const on = activeSources(list.filters, {
    agent: modules.canView("agents"),
    workflow: !agent && modules.canView("workflows"),
    task: !agent && modules.canView("apps"),
  });
  const firstPage = list.state.after === null;
  const common = {
    fetchPolicy: "cache-and-network" as const,
    pollInterval: firstPage ? POLL_MS : 0,
  };

  const agentQ = useQuery<AgentTasksPageResp>(LIST_AGENT_TASKS_PAGE, {
    ...common,
    variables: {
      orgId,
      status: null,
      workloadId: agent?.id ?? null,
      search: null,
      limit: SOURCE_LIMIT,
      after: null,
    },
    skip: !orgId || modules.loading || !on.agent,
  });
  const workflowQ = useQuery<WorkflowRunsResp>(LIST_WORKFLOW_DEFINITION_RUNS, {
    ...common,
    variables: { orgId: orgId || null, projectId: null, status: null, limit: SOURCE_LIMIT },
    skip: !orgId || modules.loading || !on.workflow,
  });
  const taskQ = useQuery<TaskRunsPageResp>(LIST_TASK_RUNS_PAGE, {
    ...common,
    variables: { limit: SOURCE_LIMIT, after: null },
    skip: modules.loading || !on.task,
  });

  const sources = { agent: agentQ, workflow: workflowQ, task: taskQ };
  const active = (Object.keys(sources) as RunKind[]).filter((k) => on[k]);

  const merged: RunRow[] = [
    ...(on.agent ? (agentQ.data?.agentTasksPage.items ?? []).map(fromAgentTask) : []),
    ...(on.workflow ? (workflowQ.data?.workflowDefinitionRuns ?? []).map(fromWorkflowRun) : []),
    ...(on.task
      ? (taskQ.data?.astroliftTaskRunsPage.items ?? []).map((r) => fromTaskRun(r, me))
      : []),
  ];
  const filtered = sortRunRows(
    filterRunRows(merged, list.filters, list.state.q, now),
    list.state.sort
  );
  const page = pageRunRows(filtered, list.state.after, list.state.pageSize);

  const failed = active.filter((k) => sources[k].error && !sources[k].data);
  const pending = active.some((k) => sources[k].loading && !sources[k].data);
  const hasAny = active.some((k) => sources[k].data);

  const held = useHeldRows(page.rows, (r) => r.key, {
    live: firstPage && !pending,
    resetKey:
      JSON.stringify(list.filters) +
      list.state.q +
      list.state.pageSize +
      JSON.stringify(list.state.sort),
  });

  const capped =
    (on.agent && Boolean(agentQ.data?.agentTasksPage.nextCursor)) ||
    (on.workflow && (workflowQ.data?.workflowDefinitionRuns.length ?? 0) >= SOURCE_LIMIT) ||
    (on.task && Boolean(taskQ.data?.astroliftTaskRunsPage.nextCursor));

  const refetchAll = () => {
    for (const k of active) void sources[k].refetch();
  };

  const [cancelTask] = useMutation<CancelTaskResp>(CANCEL_TASK);
  const [cancelWorkflow] = useCancelWorkflowInstance();

  /** Cancels each run; throws when none could be, so the confirm dialog stays open. */
  async function onCancel(runs: RunRow[]) {
    const results = await Promise.all(
      runs.map(async (r): Promise<string | null> => {
        try {
          if (r.cancel?.kind === "agent") {
            const { data } = await cancelTask({ variables: { id: r.cancel.id } });
            return data?.cancelTask.ok ? null : (data?.cancelTask.errors[0]?.message ?? "failed");
          }
          if (r.cancel?.kind === "workflow") {
            const { data } = await cancelWorkflow({
              variables: { workflowId: r.cancel.workflowId },
            });
            const res = data?.cancelWorkflowInstance;
            return res?.ok ? null : (res?.errors?.[0]?.messages?.[0] ?? "failed");
          }
          return "not cancellable";
        } catch (e) {
          return e instanceof Error ? e.message : "failed";
        }
      })
    );
    const errors = results.filter((e): e is string => e !== null);
    refetchAll();
    if (errors.length === runs.length) throw new Error(errors[0] ?? "Nothing was cancelled");
    if (errors.length > 0) {
      toast.warning(`Cancelled ${runs.length - errors.length} of ${runs.length} runs`, {
        description: errors[0],
      });
    } else {
      toast.success(runs.length === 1 ? "Run cancelled" : `Cancelled ${runs.length} runs`);
    }
  }

  const byKey = new Map(merged.map((r) => [r.key, r]));
  const allFailed = active.length > 0 && failed.length === active.length;

  return {
    list,
    embedded: Boolean(agent),
    rows: held.rows,
    newRows: { count: held.newCount, onReveal: held.reveal },
    loading: (modules.loading || !orgId || pending) && !hasAny,
    stale: pending && hasAny,
    error: allFailed
      ? { message: sources[failed[0]].error?.message ?? "The runs failed to load." }
      : null,
    onRetry: refetchAll,
    nextCursor: page.nextCursor,
    totalCount: hasAny ? filtered.length : null,
    approximateCount: capped,
    unavailable: failed.map((k) => SOURCE_LABEL[k]),
    coverage: capped
      ? `Merged from the newest ${SOURCE_LIMIT} of each kind; filters and search cover those.`
      : null,
    lookup: (key) => byKey.get(key) ?? null,
    onCancel,
  };
}
