"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { downloadText, logText, useNow } from "@/components/screens/deployments/run-support";
import { CANCEL_TASK } from "@/graphql/agents/agents.mutations";
import { AGENT_TASK_LOGS, GET_AGENT_TASK } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentTask } from "@/graphql/agents/agents.types";

import { agentLogLines, TERMINAL_STATUSES } from "./agent-run-steps";

interface TaskResp {
  agentTask: AstroliftAgentTask | null;
}
interface LogsResp {
  agentTaskLogs: string[];
}
interface CancelResp {
  cancelTask: {
    ok: boolean;
    errors: Array<{ message: string }>;
  };
}

/** The newest lines the run page reads; the one log query on the page. */
const LOG_TAIL = 200;

/**
 * Agent run detail (#1105) data: one AgentTask, read via `agentTask(id)` and
 * `agentTaskLogs(id)`. Both are tenant-scoped server-side, so no orgId arg is
 * needed. Polls while the run is non-terminal so a live run updates without a
 * reload, ticks its clock, and builds the log download. The log pane reads
 * this same query, so the page fetches the log once.
 */
export function useAgentRunDetail(taskId: string) {
  const {
    data: taskData,
    loading,
    error,
    refetch: refetchTask,
    stopPolling: stopTaskPoll,
  } = useQuery<TaskResp>(GET_AGENT_TASK, {
    variables: { id: taskId },
    fetchPolicy: "cache-and-network",
    pollInterval: 5000,
  });
  const {
    data: logsData,
    loading: logsLoading,
    error: logsError,
    refetch: refetchLogs,
    stopPolling: stopLogsPoll,
  } = useQuery<LogsResp>(AGENT_TASK_LOGS, {
    variables: { id: taskId, tail: LOG_TAIL },
    fetchPolicy: "cache-and-network",
    pollInterval: 5000,
  });

  const task = taskData?.agentTask ?? null;
  const terminal = task ? TERMINAL_STATUSES.has(task.status) : false;
  const now = useNow(Boolean(task) && !terminal);
  const [cancelTask] = useMutation<CancelResp>(CANCEL_TASK);

  /** Throws on failure so the confirm dialog stays open and shows the error. */
  async function onHardStop() {
    const { data } = await cancelTask({ variables: { id: taskId } });
    const result = data?.cancelTask;
    if (!result?.ok) {
      throw new Error(result?.errors?.[0]?.message ?? "Agent task could not be stopped");
    }
    await refetchTask();
    toast.success("Agent task stopped", {
      description: "The workload was deleted and the task is now cancelled.",
    });
  }

  // Once the run reaches a terminal state neither the task nor its logs will
  // change again: stop both polls. A run seen going terminal gets one last
  // pull of its closing tail; one that loaded finished already has it.
  const seenLive = React.useRef(false);
  React.useEffect(() => {
    if (!task) return;
    if (!terminal) {
      seenLive.current = true;
      return;
    }
    stopTaskPoll();
    stopLogsPoll();
    if (seenLive.current) {
      seenLive.current = false;
      void refetchLogs();
    }
  }, [task, terminal, stopTaskPoll, stopLogsPoll, refetchLogs]);

  const logs = logsData?.agentTaskLogs ?? [];

  return {
    taskId,
    task,
    loading,
    error: task ? null : (error?.message ?? null),
    onRetry: () => {
      void refetchTask();
    },
    terminal,
    now,
    logs,
    logsLoading: logsLoading && !logsData,
    logsError: logsError && !logsData ? logsError.message : null,
    onRetryLogs: () => {
      void refetchLogs();
    },
    onDownloadLogs: () => {
      if (!task) return;
      downloadText(
        `agent-run-${taskId.slice(0, 8)}.log`,
        logText(agentLogLines(logs, task.startedAt ?? task.createdAt))
      );
    },
    onHardStop,
  };
}

export type AgentRunDetailState = ReturnType<typeof useAgentRunDetail>;
