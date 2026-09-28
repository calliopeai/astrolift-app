"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import { RUN_AGENT, SEND_AGENT_TASK_INPUT } from "@/graphql/agents/agents.mutations";
import { GET_AGENT_DETAIL, LIST_AGENT_TASKS } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentDetail, AstroliftAgentListItem } from "@/graphql/agents/agents.types";

export interface AgentOverviewTask {
  id: string;
  status: string;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
}
interface AgentTasksResp {
  agentTasks: AgentOverviewTask[];
}
interface AgentDetailResp {
  agent: AstroliftAgentDetail | null;
}
interface RunAgentResp {
  runAstroliftAgent: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { id: string; status: string; createdAt: string } | null;
  };
}
interface SendInputResp {
  sendAgentTaskInput?: { ok: boolean; errors?: { message: string }[] };
}

/**
 * Data for the agent Overview pillar: the agent detail join (skills/tools,
 * image, brief), a 5s poll of its tasks, one-click dispatch, and overseer chat
 * input to the running task.
 */
export function useAgentOverview({
  agent,
  orgId,
}: {
  agent: AstroliftAgentListItem;
  orgId: string;
}) {
  const { data: detailData, loading: detailLoading } = useQuery<AgentDetailResp>(GET_AGENT_DETAIL, {
    variables: { orgId, slug: agent.slug },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });
  const { data: tasksData, refetch } = useQuery<AgentTasksResp>(LIST_AGENT_TASKS, {
    variables: { orgId, status: null, workloadId: agent.id },
    skip: !orgId,
    pollInterval: 5000,
    fetchPolicy: "cache-and-network",
  });

  const [runAgent, { loading: dispatching }] = useMutation<RunAgentResp>(RUN_AGENT);
  const [sendInput, { loading: sendingInput }] = useMutation<SendInputResp>(SEND_AGENT_TASK_INPUT);

  async function onDispatch() {
    try {
      const { data: res } = await runAgent({
        variables: { input: { agentSlug: agent.slug } },
      });
      const result = res?.runAstroliftAgent;
      if (!result?.ok) {
        throw new Error(result?.errors?.[0]?.message ?? "Dispatch failed");
      }
      toast.success(`Dispatched ${agent.name}`);
      await refetch();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(`Couldn't dispatch ${agent.name}`, { description: message });
    }
  }

  /** Queue a message for the running task; resolves true once it is queued. */
  async function onSendInput(taskId: string, message: string): Promise<boolean> {
    try {
      const response = await sendInput({ variables: { taskId, message } });
      const result = response.data?.sendAgentTaskInput;
      if (!result?.ok) throw new Error(result?.errors?.[0]?.message ?? "Message was not queued");
      toast.success("Message queued for the next agent turn");
      return true;
    } catch (error) {
      toast.error("Could not reach the overseer", {
        description: error instanceof Error ? error.message : String(error),
      });
      return false;
    }
  }

  return {
    agent,
    detail: detailData?.agent ?? null,
    detailLoading,
    tasks: tasksData?.agentTasks ?? [],
    dispatching,
    sendingInput,
    onDispatch,
    onSendInput,
  };
}

export type AgentOverviewProps = ReturnType<typeof useAgentOverview>;
