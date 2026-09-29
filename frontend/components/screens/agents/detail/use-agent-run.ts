"use client";

import { NetworkStatus } from "@apollo/client";
import { useQuery } from "@apollo/client/react";

import { useHeldRows } from "@/components/list/use-held-rows";
import { useListState } from "@/components/list/use-list-state";
import { LIST_AGENT_TASKS_PAGE } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";

import { AGENT_RUNS_LIST, runsPageVariables } from "./agent-runs-list";

/** One agent execution, as the page query returns it. */
export interface AgentTask {
  id: string;
  status: string;
  callbackUrl: string;
  result: unknown;
  failureMessage?: string | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  vncEnabled: boolean;
  vncUrl: string;
  snapshotUrl: string | null;
}
interface TasksPageResp {
  agentTasksPage: { items: AgentTask[]; nextCursor: string | null; totalCount: number | null };
}

const POLL_MS = 5000;

/**
 * The data half of the agent's Runs tab (spec 44 §5.1): list state in the
 * URL, one cursor page of `agentTasksPage` scoped to this agent
 * (`workloadId` is the agent's Workload id), polled on the first page so a
 * new run and its state changes show without a reload, with new rows held
 * behind the pill. Run now is the frame's; this tab only lists.
 */
export function useAgentRun(agent: Pick<AstroliftAgentListItem, "id">, orgId: string) {
  const list = useListState(AGENT_RUNS_LIST);
  const { state, filters } = list;
  const firstPage = state.after === null;

  const page = useQuery<TasksPageResp>(LIST_AGENT_TASKS_PAGE, {
    variables: runsPageVariables(orgId, agent.id, filters, state.q, state.pageSize, state.after),
    skip: !orgId,
    fetchPolicy: "cache-and-network",
    pollInterval: firstPage ? POLL_MS : 0,
  });
  const data = page.data?.agentTasksPage;

  const held = useHeldRows(data?.items ?? [], (t) => t.id, {
    live: firstPage,
    resetKey: JSON.stringify(filters) + state.q + state.pageSize,
  });

  return {
    list,
    rows: held.rows,
    newRows: { count: held.newCount, onReveal: held.reveal },
    loading: !orgId || (page.loading && !data),
    stale: page.networkStatus === NetworkStatus.setVariables && Boolean(data),
    error: page.error && !data ? { message: page.error.message } : null,
    onRetry: () => void page.refetch(),
    nextCursor: data?.nextCursor ?? null,
    totalCount: data?.totalCount ?? null,
  };
}

export type AgentRunState = ReturnType<typeof useAgentRun>;
