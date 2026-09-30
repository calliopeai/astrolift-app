"use client";

import { useQuery } from "@apollo/client/react";

import { GET_AGENT_TASK } from "@/graphql/agents/agents.queries";

export interface VncPopoutTask {
  id: string;
  status: string;
  startedAt: string | null;
  vncEnabled: boolean;
  vncUrl: string;
  snapshotUrl: string | null;
}

interface GetAgentTaskData {
  agentTask: VncPopoutTask | null;
}

/**
 * The pop-out session's task. Polls so that when it stops running the viewer
 * flips to a clear "session ended" state instead of leaving a dead canvas.
 */
export function useAgentVncPopout(taskId: string) {
  const { data, loading, error, refetch } = useQuery<GetAgentTaskData>(GET_AGENT_TASK, {
    variables: { id: taskId },
    fetchPolicy: "cache-and-network",
    pollInterval: 5000,
  });

  return {
    taskId,
    task: data?.agentTask ?? null,
    loading,
    error: data ? null : (error?.message ?? null),
    onRetry: () => {
      void refetch().catch(() => {});
    },
  };
}

export type AgentVncPopoutState = ReturnType<typeof useAgentVncPopout>;
