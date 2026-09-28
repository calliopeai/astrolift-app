"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_AGENT_TASKS } from "@/graphql/agents/agents.queries";

export type AgentTask = {
  id: string;
  status: string;
  callbackUrl: string;
  result: unknown;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  vncEnabled: boolean;
  vncUrl: string;
};

type AgentTasksData = {
  agentTasks: AgentTask[];
};

/** Running agent tasks, polled every 5s. The data half of ActiveTasksPanel. */
export function useActiveAgentTasks(orgId: string) {
  const { data, loading } = useQuery<AgentTasksData>(LIST_AGENT_TASKS, {
    variables: { orgId, status: "running" },
    pollInterval: 5000,
    skip: !orgId,
  });
  return { tasks: data?.agentTasks ?? [], loading };
}

// Terminal AgentTask.Status values (backend contract): there is no
// "succeeded" — a successful task is "completed".
const TERMINAL_STATUSES = ["completed", "failed", "timed_out", "cancelled"];

/** Completed / terminal agent tasks. The data half of TaskHistoryPanel. */
export function useAgentTaskHistory(orgId: string) {
  const { data, loading } = useQuery<AgentTasksData>(LIST_AGENT_TASKS, {
    variables: { orgId },
    skip: !orgId,
  });
  const tasks = (data?.agentTasks ?? []).filter((t) => TERMINAL_STATUSES.includes(t.status));
  return { tasks, loading };
}
