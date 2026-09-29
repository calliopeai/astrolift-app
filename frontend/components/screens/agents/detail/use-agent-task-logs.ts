"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import {
  downloadTextFile,
  formatLogFile,
  logFilename,
} from "@/components/screens/apps/deployments/app-log-lines";
import { AGENT_TASK_LOGS } from "@/graphql/agents/agents.queries";

import { agentLogLines } from "./agent-log-lines";

interface AgentTaskLogsResp {
  agentTaskLogs: string[];
}

const LOG_TAIL = 200;

/**
 * The tail of one run's logs, polled, as LogView lines, with the download.
 * The data half of AgentTaskLogsView.
 */
export function useAgentTaskLogs(taskId: string) {
  const { data, loading, error, refetch } = useQuery<AgentTaskLogsResp>(AGENT_TASK_LOGS, {
    variables: { id: taskId, tail: LOG_TAIL },
    pollInterval: 5000,
    fetchPolicy: "cache-and-network",
  });
  const raw = data?.agentTaskLogs;
  const lines = React.useMemo(() => agentLogLines(raw ?? []), [raw]);

  return {
    lines,
    loading: loading && !data,
    error: error && !data ? error.message : null,
    onRetry: () => void refetch(),
    onDownload: () =>
      downloadTextFile(logFilename([{ value: taskId, fallback: "run" }]), formatLogFile(lines)),
  };
}
