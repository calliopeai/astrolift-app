"use client";

import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { useHeldRows } from "@/components/list/use-held-rows";
import { useListState } from "@/components/list/use-list-state";
import { CANCEL_TASK, RETRY_AGENT_TASK } from "@/graphql/agents/agents.mutations";
import {
  AGENT_TASKS_LIST_PAGE,
  AGENT_UPCOMING_RUNS,
  AGENTS_AREA_RUN_AUDIT,
} from "@/graphql/agents/agents.queries";
import type { AstroliftAgentUpcomingRun } from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useModules } from "@/graphql/user/user.hooks";
import { useCancelWorkflowInstance } from "@/graphql/workflows/workflows.hooks";
import { GET_WORKFLOW_DEFINITION_RUN } from "@/graphql/workflows/tiered.queries";

import {
  AGENT_RUNS_LIST,
  type AgentTaskSource,
  agentTasksVariables,
  fromAgentTask,
  fromRunAuditItem,
  fromUpcomingRun,
  type RunAuditSource,
  type RunKind,
  type RunRow,
  RUNS_LIST,
  runAuditVariables,
  UPCOMING,
  upcomingNextCursor,
  upcomingVariables,
} from "./runs-list";
import type { RunsScreenProps } from "./RunsScreen";

const POLL_MS = 10_000;
/** The running agent tasks the page reads for Watch live; a page shows at most 100 rows. */
const LIVE_TASKS_LIMIT = 100;

interface RunAuditResp {
  astroliftRunAudit: {
    items: RunAuditSource[];
    nextCursor: string | null;
    totalCount: number | null;
  };
}
interface AgentTasksPageResp {
  agentTasksPage: {
    items: AgentTaskSource[];
    nextCursor: string | null;
    totalCount: number | null;
  };
}
interface UpcomingResp {
  agentUpcomingRuns: {
    items: AstroliftAgentUpcomingRun[];
    totalCount: number;
    page: number;
    pageSize: number;
  };
}
interface CancelTaskResp {
  cancelTask: { ok: boolean; errors: { message: string }[] };
}
interface RetryTaskResp {
  retryAgentTask: { ok: boolean; errors: { message: string }[] };
}
interface WorkflowRunResp {
  workflowDefinitionRun: { temporalWorkflowId: string } | null;
}

export interface UseRunsOptions {
  /** An agent's Runs tab: only this agent's runs (its Workload id scopes the query). */
  agent?: { id: string; slug: string };
}

/**
 * The Runs list's data half, for the Runs page or an agent's Runs tab. One
 * server read answers the list state (spec 44 §5.1): the Runs page reads
 * `astroliftRunAudit` held to the kinds the viewer's modules show, an
 * agent's tab reads `agentTasksPage`, and the Scheduled view reads
 * `agentUpcomingRuns`. The first page polls, with new rows held behind the
 * pill. Cancel and Retry act on the selection.
 */
export function useRuns({ agent }: UseRunsOptions = {}): RunsScreenProps {
  const client = useApolloClient();
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const modules = useModules();
  const list = useListState(agent ? AGENT_RUNS_LIST : RUNS_LIST);
  const { state, filters } = list;
  const [now] = React.useState(() => Date.now());
  const firstPage = state.after === null;
  const upcoming = !agent && Boolean(filters[UPCOMING]);
  const common = {
    fetchPolicy: "cache-and-network" as const,
    pollInterval: firstPage ? POLL_MS : 0,
  };

  const kinds: RunKind[] = [
    ...(modules.canView("agents") ? (["agent"] as const) : []),
    ...(modules.canView("workflows") ? (["workflow"] as const) : []),
    ...(modules.canView("apps") ? (["task"] as const) : []),
  ];
  const auditVars = runAuditVariables(
    { filters, q: state.q, sort: state.sort, after: state.after, pageSize: state.pageSize },
    kinds,
    now
  );
  const auditQ = useQuery<RunAuditResp>(AGENTS_AREA_RUN_AUDIT, {
    ...common,
    variables: auditVars ?? undefined,
    skip: Boolean(agent) || upcoming || modules.loading || auditVars === null,
  });

  const tasksQ = useQuery<AgentTasksPageResp>(AGENT_TASKS_LIST_PAGE, {
    ...common,
    variables: agentTasksVariables(orgId, agent?.id ?? "", {
      filters,
      q: state.q,
      sort: state.sort,
      after: state.after,
      pageSize: state.pageSize,
    }),
    skip: !agent || !orgId,
  });

  const upcomingVars = upcomingVariables(orgId, {
    filters,
    q: state.q,
    after: state.after,
    pageSize: state.pageSize,
  });
  const upcomingQ = useQuery<UpcomingResp>(AGENT_UPCOMING_RUNS, {
    ...common,
    variables: upcomingVars ?? undefined,
    skip: !upcoming || !orgId || upcomingVars === null,
  });

  // The audit row carries no VNC coordinates: Watch live reads the org's
  // running agent tasks while the page shows one.
  // Rows on screen answer the previous list state while the next loads.
  const auditData = auditQ.data ?? auditQ.previousData;
  const tasksData = tasksQ.data ?? tasksQ.previousData;
  const upcomingData = upcomingQ.data ?? upcomingQ.previousData;
  const auditItems = auditData?.astroliftRunAudit.items ?? [];
  const anyRunningAgent = auditItems.some((r) => r.kind === "agent" && r.status === "running");
  const liveQ = useQuery<AgentTasksPageResp>(AGENT_TASKS_LIST_PAGE, {
    ...common,
    variables: agentTasksVariables(orgId, null, {
      filters: { status: "running" },
      q: "",
      sort: [],
      after: null,
      pageSize: LIVE_TASKS_LIMIT,
    }),
    skip: Boolean(agent) || upcoming || !orgId || !anyRunningAgent,
  });
  const vncById = new Map((liveQ.data?.agentTasksPage.items ?? []).map((t) => [t.id, t]));

  // The one source this list state reads.
  const source = agent ? tasksQ : upcoming ? upcomingQ : auditQ;
  const nothingToAsk = agent ? !orgId : upcoming ? upcomingVars === null : auditVars === null;

  let pageRows: RunRow[] = [];
  let nextCursor: string | null = null;
  let totalCount: number | null = null;
  if (agent && tasksData) {
    pageRows = tasksData.agentTasksPage.items.map(fromAgentTask);
    nextCursor = tasksData.agentTasksPage.nextCursor;
    totalCount = tasksData.agentTasksPage.totalCount;
  } else if (upcoming && upcomingData) {
    const p = upcomingData.agentUpcomingRuns;
    pageRows = p.items.map(fromUpcomingRun);
    nextCursor = upcomingNextCursor(p.page, p.pageSize, p.totalCount);
    totalCount = p.totalCount;
  } else if (!agent && !upcoming && auditData) {
    pageRows = auditItems.map((r) => fromRunAuditItem(r, vncById.get(r.id)));
    nextCursor = auditData.astroliftRunAudit.nextCursor;
    totalCount = auditData.astroliftRunAudit.totalCount;
  } else if (nothingToAsk && !modules.loading) {
    // A chip only another kind of run could match: nothing to ask, nothing to show.
    totalCount = 0;
  }

  const shown = agent ? tasksData : upcoming ? upcomingData : auditData;
  const hasData = Boolean(shown) || (nothingToAsk && !modules.loading);
  const pending = source.loading && !source.data;

  const held = useHeldRows(pageRows, (r) => r.key, {
    live: firstPage && !pending,
    resetKey:
      JSON.stringify(filters) + state.q + state.pageSize + JSON.stringify(state.sort) + state.view,
  });

  // The bulk bar acts on the selected runs on screen, as they read now: a
  // run selected on another page is not acted on, and the button's count
  // says how many will be.
  const byKey = new Map(pageRows.map((r) => [r.key, r]));

  const refetch = () => {
    if (!nothingToAsk) void source.refetch();
  };

  const [cancelTask] = useMutation<CancelTaskResp>(CANCEL_TASK);
  const [retryTask] = useMutation<RetryTaskResp>(RETRY_AGENT_TASK);
  const [cancelWorkflow] = useCancelWorkflowInstance();

  async function cancelOne(r: RunRow): Promise<string | null> {
    try {
      if (r.cancel?.kind === "agent") {
        const { data } = await cancelTask({ variables: { id: r.cancel.id } });
        return data?.cancelTask.ok ? null : (data?.cancelTask.errors[0]?.message ?? "failed");
      }
      if (r.cancel?.kind === "workflow") {
        // The audit row names the run; Temporal cancels by its workflow id.
        let workflowId = r.cancel.workflowId;
        if (!workflowId && r.cancel.guid) {
          const { data: run } = await client.query<WorkflowRunResp>({
            query: GET_WORKFLOW_DEFINITION_RUN,
            variables: { guid: r.cancel.guid, orgId: orgId || null },
            fetchPolicy: "network-only",
          });
          workflowId = run?.workflowDefinitionRun?.temporalWorkflowId;
        }
        if (!workflowId) return "the workflow run was not found";
        const { data } = await cancelWorkflow({ variables: { workflowId } });
        const res = data?.cancelWorkflowInstance;
        return res?.ok ? null : (res?.errors?.[0]?.messages?.[0] ?? "failed");
      }
      return "not cancellable";
    } catch (e) {
      return e instanceof Error ? e.message : "failed";
    }
  }

  async function retryOne(r: RunRow): Promise<string | null> {
    if (!r.retry) return "not retryable";
    try {
      const { data } = await retryTask({ variables: { id: r.retry.id } });
      return data?.retryAgentTask.ok ? null : (data?.retryAgentTask.errors[0]?.message ?? "failed");
    } catch (e) {
      return e instanceof Error ? e.message : "failed";
    }
  }

  /** Runs `act` on each run; throws when none succeeded, so the dialog stays open. */
  async function each(
    runs: RunRow[],
    act: (r: RunRow) => Promise<string | null>,
    words: { one: string; many: (n: number) => string; some: (n: number, of: number) => string }
  ) {
    const errors = (await Promise.all(runs.map(act))).filter((e): e is string => e !== null);
    refetch();
    if (errors.length === runs.length) throw new Error(errors[0] ?? "Nothing changed");
    if (errors.length > 0) {
      toast.warning(words.some(runs.length - errors.length, runs.length), {
        description: errors[0],
      });
    } else {
      toast.success(runs.length === 1 ? words.one : words.many(runs.length));
    }
  }

  const onCancel = (runs: RunRow[]) =>
    each(runs, cancelOne, {
      one: "Run cancelled",
      many: (n) => `Cancelled ${n} runs`,
      some: (n, of) => `Cancelled ${n} of ${of} runs`,
    });

  const onRetryRuns = (runs: RunRow[]) =>
    each(runs, retryOne, {
      one: "Run started again",
      many: (n) => `Started ${n} runs again`,
      some: (n, of) => `Started ${n} of ${of} runs again`,
    });

  return {
    list,
    embedded: Boolean(agent),
    rows: held.rows,
    newRows: { count: held.newCount, onReveal: held.reveal },
    loading: (modules.loading || (!orgId && !nothingToAsk) || pending) && !hasData,
    stale: pending && Boolean(shown),
    error: source.error && !shown ? { message: source.error.message } : null,
    onRetry: refetch,
    nextCursor,
    totalCount,
    approximateCount: false,
    lookup: (key) => byKey.get(key) ?? null,
    onCancel,
    onRetryRuns,
  };
}
