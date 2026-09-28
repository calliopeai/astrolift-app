"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { CANCEL_TASK } from "@/graphql/agents/agents.mutations";
import { AGENT_TASK_LOGS, GET_AGENT_TASK } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentTask } from "@/graphql/agents/agents.types";

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

// Terminal AgentTask.Status values (backend contract, mirrors agents-client):
// a successful task is "completed", not "succeeded". Reaching a terminal state
// stops the live poll.
const TERMINAL_STATUSES = ["completed", "failed", "timed_out", "cancelled"];

/**
 * Agent run detail (#1105) data: one AgentTask, read via `agentTask(id)` and
 * `agentTaskLogs(id)`. Both are tenant-scoped server-side, so no orgId arg is
 * needed. Polls while the run is non-terminal so a live run updates without a
 * reload.
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
  const { data: logsData, stopPolling: stopLogsPoll } = useQuery<LogsResp>(AGENT_TASK_LOGS, {
    variables: { id: taskId, tail: 200 },
    fetchPolicy: "cache-and-network",
    pollInterval: 5000,
  });

  const task = taskData?.agentTask ?? null;
  const terminal = task ? TERMINAL_STATUSES.includes(task.status) : false;
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
  // change again — stop both polls.
  React.useEffect(() => {
    if (terminal) {
      stopTaskPoll();
      stopLogsPoll();
    }
  }, [terminal, stopTaskPoll, stopLogsPoll]);

  return {
    taskId,
    task,
    loading,
    error: error ? error.message : null,
    terminal,
    logs: logsData?.agentTaskLogs ?? [],
    onHardStop,
  };
}

export type AgentRunDetailState = ReturnType<typeof useAgentRunDetail>;
