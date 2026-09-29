"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_SUMMARY_MAX } from "@/components/list/ListSummary";
import { LIST_AGENT_TASKS_PAGE } from "@/graphql/agents/agents.queries";

export interface AgentTask {
  id: string;
  status: string;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  failureMessage: string | null;
}
interface TasksPageResp {
  agentTasksPage: { items: AgentTask[] };
}

/**
 * The data half of Logs & metrics › Live runs: this agent's latest run, the
 * one whose log the section shows. The same page query and variables as
 * the Overview's (five newest, polled), so moving between the two reads
 * one cache entry. Newest first spans every status, so a run that failed at
 * spawn (no pod, no logs) surfaces with its `failureMessage`.
 */
export function useAgentObserve(agent: { id: string }, orgId: string) {
  const { data, loading, error, refetch } = useQuery<TasksPageResp>(LIST_AGENT_TASKS_PAGE, {
    variables: {
      orgId,
      workloadId: agent.id,
      status: null,
      search: null,
      limit: LIST_SUMMARY_MAX,
      after: null,
    },
    skip: !orgId,
    pollInterval: 5000,
    fetchPolicy: "cache-and-network",
  });

  return {
    latest: data?.agentTasksPage.items[0] ?? null,
    loading: !orgId || (loading && !data),
    error: error && !data ? error.message : null,
    onRetry: () => void refetch(),
  };
}
