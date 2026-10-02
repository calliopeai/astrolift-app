"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { LIST_SUMMARY_MAX } from "@/components/list/ListSummary";
import { useNow } from "@/components/screens/deployments/run-support";
import { SEND_AGENT_TASK_INPUT } from "@/graphql/agents/agents.mutations";
import {
  GET_AGENT_DETAIL,
  LIST_AGENT_FLEET,
  LIST_AGENT_TASKS_PAGE,
} from "@/graphql/agents/agents.queries";
import type { AstroliftAgentDetail, AstroliftAgentListItem } from "@/graphql/agents/agents.types";

import { agentFleetSnapshot } from "./agent-fleet-snapshot";

export interface AgentOverviewTask {
  id: string;
  status: string;
  failureMessage?: string | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
}
interface TasksPageResp {
  agentTasksPage: { items: AgentOverviewTask[]; totalCount: number | null };
}
interface AgentDetailResp {
  agent: AstroliftAgentDetail | null;
}
interface FleetResp {
  agentFleet: AstroliftAgentListItem[];
}
interface SendInputResp {
  sendAgentTaskInput?: {
    ok: boolean;
    errors?: { message: string }[];
    data?: { id: string } | null;
  };
}

const POLL_MS = 5000;

/**
 * Data for the agent Overview (spec 44 §5.2; Leo's page rules 1 and 2).
 * Three reads, each for what the tab shows:
 *
 *   - the latest runs: one page of `agentTasksPage`, five rows and the
 *     count, polled; the Latest run panel and the Recent runs summary share it,
 *   - the detail join (image, brief, skills and tools), the same query and
 *     variables Skills & tools and Build read, so Apollo serves it once,
 *   - the fleet, read from the cache the frame filled (`agentFleet`).
 *
 * Plus the overseer input to the running run. Run now is the frame's.
 */
export function useAgentOverview({
  agent,
  orgId,
}: {
  agent: AstroliftAgentListItem;
  orgId: string;
}) {
  const t = useTranslations("agentFrame.input");
  const router = useRouter();
  const detailQ = useQuery<AgentDetailResp>(GET_AGENT_DETAIL, {
    variables: { orgId, slug: agent.slug },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });
  const tasksQ = useQuery<TasksPageResp>(LIST_AGENT_TASKS_PAGE, {
    variables: {
      orgId,
      workloadId: agent.id,
      status: null,
      search: null,
      limit: LIST_SUMMARY_MAX,
      after: null,
    },
    skip: !orgId,
    pollInterval: POLL_MS,
    fetchPolicy: "cache-and-network",
  });
  const fleetQ = useQuery<FleetResp>(LIST_AGENT_FLEET, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-first",
  });

  const [sendInput, { loading: sendingInput }] = useMutation<SendInputResp>(SEND_AGENT_TASK_INPUT);

  const tasks = React.useMemo(() => tasksQ.data?.agentTasksPage.items ?? [], [tasksQ.data]);
  const fleetAgents = fleetQ.data?.agentFleet;
  // The clock the viz ages runs against, read again at the poll's pace.
  const now = useNow(true, POLL_MS);
  const fleet = React.useMemo(
    () => (fleetAgents ? agentFleetSnapshot(fleetAgents, agent.id, tasks, now) : null),
    [fleetAgents, agent.id, tasks, now]
  );

  /** Queue a message for the running run; resolves true once it is queued. */
  async function onSendInput(taskId: string, message: string): Promise<boolean> {
    try {
      const response = await sendInput({ variables: { taskId, message } });
      const result = response.data?.sendAgentTaskInput;
      if (!result?.ok) throw new Error(result?.errors?.[0]?.message ?? t("notQueued"));
      if (!result.data?.id?.trim()) throw new Error(t("unconfirmed"));
      toast.success(t("queued"));
      return true;
    } catch (error) {
      toast.error(t("failed"), {
        description: error instanceof Error ? error.message : String(error),
      });
      return false;
    }
  }

  return {
    agent,
    detail: detailQ.data?.agent ?? null,
    detailLoading: detailQ.loading && !detailQ.data,
    detailError: detailQ.error && !detailQ.data ? detailQ.error.message : null,
    onRetryDetail: () => void detailQ.refetch(),
    runs: {
      rows: tasks,
      count: tasksQ.data?.agentTasksPage.totalCount ?? null,
      loading: !orgId || (tasksQ.loading && !tasksQ.data),
      error: tasksQ.error && !tasksQ.data ? tasksQ.error.message : null,
      onRetry: () => void tasksQ.refetch(),
    },
    fleet,
    onSelectAgent: (agentId: string) => {
      const slug = fleetAgents?.find((a) => a.id === agentId)?.slug;
      if (slug && slug !== agent.slug) router.push(`/agents/${encodeURIComponent(slug)}`);
    },
    sendingInput,
    onSendInput,
  };
}

export type AgentOverviewProps = ReturnType<typeof useAgentOverview>;
