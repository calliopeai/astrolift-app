"use client";

import { useQuery } from "@apollo/client/react";

import { AGENT_TASK_LOGS } from "@/graphql/agents/agents.queries";

interface AgentTaskLogsResp {
  agentTaskLogs: string[];
}

const LOG_TAIL = 200;

/** The tail of one run's logs, polled. The data half of AgentTaskLogsView. */
export function useAgentTaskLogs(taskId: string) {
  const { data, loading } = useQuery<AgentTaskLogsResp>(AGENT_TASK_LOGS, {
    variables: { id: taskId, tail: LOG_TAIL },
    pollInterval: 5000,
    fetchPolicy: "cache-and-network",
  });

  return { lines: data?.agentTaskLogs ?? [], loading };
}
