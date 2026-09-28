"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { RUN_AGENT } from "@/graphql/agents/agents.mutations";
import { LIST_AGENT_TASKS } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";

// One agent execution. Mirrors the AgentTask row shape used by the fleet
// Active/History tables in `agents-client.tsx`; LIST_AGENT_TASKS is the same
// operation, here scoped to a single agent via `workloadId`.
export interface AgentTask {
  id: string;
  status: string;
  callbackUrl: string;
  result: unknown;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  vncEnabled: boolean;
  vncUrl: string;
  snapshotUrl: string | null;
}
interface AgentTasksResp {
  agentTasks: AgentTask[];
}

interface RunAgentResp {
  runAstroliftAgent: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { id: string; status: string; createdAt: string } | null;
  };
}

/**
 * Data for an agent's Run tab (spec 33 PR-10).
 *
 *   - **Dispatch now** fires `runAstroliftAgent` (PR-1) for this agent's
 *     `slug`, then refetches the executions list so the new run appears.
 *   - **Executions** is the per-agent runs list, scoped via
 *     `agentTasks(workloadId:)` (PR-2) and polled every 5s so a freshly
 *     dispatched run (and its state transitions) shows without a reload.
 *
 * `agent.id` is the agent's Workload id — the same key `agentLiveStatus` is
 * merged on in the registry list — so it is what scopes `agentTasks`.
 */
export function useAgentRun(agent: AstroliftAgentListItem, orgId: string) {
  const { data, loading, refetch } = useQuery<AgentTasksResp>(LIST_AGENT_TASKS, {
    variables: { orgId, status: null, workloadId: agent.id },
    skip: !orgId,
    pollInterval: 5000,
    fetchPolicy: "cache-and-network",
  });
  const router = useRouter();

  const [runAgent, { loading: dispatching }] = useMutation<RunAgentResp>(RUN_AGENT);

  async function onDispatch(): Promise<boolean> {
    try {
      const { data: res } = await runAgent({
        variables: { input: { agentSlug: agent.slug } },
      });
      const result = res?.runAstroliftAgent;
      if (!result?.ok) {
        throw new Error(result?.errors?.[0]?.message ?? "Dispatch failed");
      }
      toast.success(`Dispatched ${agent.name}`);
      // Pull the new run into the list immediately; the 5s poll then tracks
      // its state. awaitRefetchQueries-style: we await so the row is present
      // before the success toast settles.
      await refetch();
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(`Couldn't dispatch ${agent.name}`, { description: message });
      return false;
    }
  }

  // Sort newest-first by createdAt so a just-dispatched run lands at the top.
  const rows = React.useMemo(
    () =>
      [...(data?.agentTasks ?? [])].sort((a, b) =>
        (b.createdAt ?? "").localeCompare(a.createdAt ?? "")
      ),
    [data?.agentTasks]
  );

  return {
    rows,
    loading,
    dispatching,
    onDispatch,
    onOpenRun: (taskId: string) => router.push(`/agents/runs/${encodeURIComponent(taskId)}`),
  };
}

export type AgentRunState = ReturnType<typeof useAgentRun>;
