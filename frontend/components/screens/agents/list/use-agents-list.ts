"use client";

import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { useListState } from "@/components/list/use-list-state";
import { RUN_AGENT } from "@/graphql/agents/agents.mutations";
import {
  AGENT_FLEET_LIST_PAGE,
  AGENT_UPCOMING_RUNS,
  LIST_AGENT_TASKS,
} from "@/graphql/agents/agents.queries";
import type {
  AstroliftAgentFleetRow,
  AstroliftAgentUpcomingRun,
} from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useModules } from "@/graphql/user/user.hooks";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { AGENTS_LIST, type AgentRow, agentFleetPageVariables, toAgentRow } from "./agents-list";

interface FleetPageResp {
  agentFleetPage: {
    items: AstroliftAgentFleetRow[];
    totalCount: number | null;
    page: number | null;
    pageSize: number | null;
  };
}
interface UpcomingResp {
  agentUpcomingRuns: { items: AstroliftAgentUpcomingRun[] };
}
interface RunAgentResp {
  runAstroliftAgent: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { id: string } | null;
  };
}

/** Status is live state; the page re-reads it on the registry's old cadence. */
const POLL_MS = 15_000;

/**
 * The Agents list: URL list state in, one numbered page of `agentFleetPage`
 * out (spec 44 §5.1, #2155), polled every 15s. The server filters, searches,
 * sorts and counts; the hook adds the next firing of each scheduled agent on
 * the page from `agentUpcomingRuns`. Run now fires `runAstroliftAgent` and
 * opens the agent's Runs tab. The data half of AgentsListScreen.
 */
export function useAgentsList() {
  const list = useListState(AGENTS_LIST);
  const { state } = list;
  const router = useRouter();
  const client = useApolloClient();
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const { canCreate } = useModules();
  const perms = useMyPermissions();

  const fleet = useQuery<FleetPageResp>(AGENT_FLEET_LIST_PAGE, {
    variables: agentFleetPageVariables(orgId, {
      q: state.q,
      filters: list.filters,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    }),
    skip: !orgId,
    pollInterval: POLL_MS,
    fetchPolicy: "cache-and-network",
  });
  const data = fleet.data ?? fleet.previousData;
  const items = data?.agentFleetPage.items ?? [];

  // "Next in 3h" for the scheduled agents on this page only. Best effort:
  // when it fails the rows read "Scheduled".
  const scheduled = items.filter((a) => a.status === "scheduled").map((a) => a.slug);
  const upcoming = useQuery<UpcomingResp>(AGENT_UPCOMING_RUNS, {
    variables: { orgId, agent: scheduled, perAgent: 1, page: 1, pageSize: scheduled.length },
    skip: !orgId || scheduled.length === 0,
    pollInterval: POLL_MS,
    fetchPolicy: "cache-and-network",
  });
  const nextBySlug = new Map<string, string>();
  for (const u of (upcoming.data ?? upcoming.previousData)?.agentUpcomingRuns.items ?? []) {
    if (!nextBySlug.has(u.agentSlug)) nextBySlug.set(u.agentSlug, u.scheduledAt);
  }
  const rows: AgentRow[] = items.map((a) => toAgentRow(a, nextBySlug.get(a.slug) ?? null));

  const [runAgent, { loading: dispatching }] = useMutation<RunAgentResp>(RUN_AGENT);

  async function onRun(agent: Pick<AgentRow, "slug" | "name">): Promise<boolean> {
    try {
      const { data } = await runAgent({ variables: { input: { agentSlug: agent.slug } } });
      const result = data?.runAstroliftAgent;
      if (!result?.ok) throw new Error(result?.errors?.[0]?.message ?? "Dispatch failed");
      toast.success(`Dispatched ${agent.name}`);
      await client.refetchQueries({
        include: [LIST_AGENT_TASKS, AGENT_FLEET_LIST_PAGE],
      });
      router.push(`/agents/${encodeURIComponent(agent.slug)}/runs`);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(`Couldn't dispatch ${agent.name}`, { description: message });
      return false;
    }
  }

  return {
    list,
    rows,
    totalCount: data?.agentFleetPage.totalCount ?? rows.length,
    loading: !orgId || (fleet.loading && !data),
    // Rows on screen answer the previous list state while the next loads.
    stale: fleet.loading && !fleet.data && Boolean(data),
    error: fleet.error && !data ? { message: fleet.error.message } : null,
    onRetry: () => void fleet.refetch(),
    // The create affordance follows the Agents module's server-side canCreate
    // (spec 36 §1.3); Run now is `agent.dispatch`, the grant the server checks.
    // Optimistic while the permission set loads, as `Can` is.
    canCreate: canCreate("agents"),
    canRun: (perms.loading && perms.granted.size === 0) || perms.can("agent.dispatch"),
    dispatching,
    onRun,
  };
}
