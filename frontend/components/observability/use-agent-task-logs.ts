"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { AGENT_TASK_LOGS } from "@/graphql/agents/agents.queries";

interface LogsResp {
  agentTaskLogs: string[];
}

// Live-tail cadence. Faster than the run-detail's 5s status poll so streamed
// output feels live, but only runs WHILE the run is active — see `pollInterval`.
const POLL_MS = 2000;
// Newest-N lines requested per poll when a caller doesn't narrow it.
const DEFAULT_TAIL = 500;

/**
 * The newest ``tail`` lines of an agent run's logs. There is no log-stream
 * subscription, so this polls every {@link POLL_MS} while the run is
 * ``running``, then one final fetch once it goes terminal (a final pull
 * captures the full closing tail). The data half of LiveLogTerminal.
 */
export function useAgentTaskLogs(taskId: string, running: boolean, tail = DEFAULT_TAIL) {
  const prevRunningRef = React.useRef(running);
  const { data, error, loading, refetch } = useQuery<LogsResp>(AGENT_TASK_LOGS, {
    variables: { id: taskId, tail },
    fetchPolicy: "cache-and-network",
    // Poll only while live; a terminal run keeps the single mount fetch.
    pollInterval: running ? POLL_MS : 0,
  });

  // One final fetch on the running → terminal transition so the completed run
  // shows its full closing tail. Polling has already stopped (pollInterval → 0).
  React.useEffect(() => {
    if (prevRunningRef.current && !running) {
      void refetch();
    }
    prevRunningRef.current = running;
  }, [running, refetch]);

  return {
    lines: data?.agentTaskLogs ?? null,
    error: error?.message ?? null,
    loading,
  };
}
