"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_AGENT_TASKS } from "@/graphql/agents/agents.queries";

export interface AgentTask {
  id: string;
  status: string;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  failureMessage: string | null;
}
interface AgentTasksResp {
  agentTasks: AgentTask[];
}

/**
 * The data half of the agent Observe tab (spec 33 PR-9): the org's runs,
 * newest first, and the most recent one.
 *
 * Spans every status (not just running) so a run that failed at spawn, which
 * never starts a pod and has empty logs, surfaces with its `failureMessage`
 * instead of a blank box. Sorted by `createdAt`, since a spawn-failed run has
 * no `startedAt`. (Per-agent scoping arrives with the PR-10 executions list +
 * `agentTasks(workloadId:)`.)
 */
export function useAgentObserve(orgId: string) {
  const { data, loading } = useQuery<AgentTasksResp>(LIST_AGENT_TASKS, {
    variables: { orgId, status: null },
    skip: !orgId,
    pollInterval: 5000,
    fetchPolicy: "cache-and-network",
  });

  const tasks = data?.agentTasks ?? [];
  const latest =
    [...tasks].sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""))[0] ?? null;

  return { tasks, latest, loading };
}
