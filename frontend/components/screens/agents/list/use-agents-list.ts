"use client";

import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { useListState } from "@/components/list/use-list-state";
import { RUN_AGENT } from "@/graphql/agents/agents.mutations";
import {
  LIST_AGENT_ENVIRONMENT_SPECS,
  LIST_AGENT_FLEET,
  LIST_AGENT_LIVE_STATUS,
  LIST_AGENT_TASKS,
} from "@/graphql/agents/agents.queries";
import type {
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
  AstroliftAgentLiveStatus,
} from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_MY_APPS_PAGE } from "@/graphql/registry/registry.queries";
import { useModules } from "@/graphql/user/user.hooks";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { AGENTS_LIST, type AgentRow, joinAgents, selectAgents } from "./agents-list";

interface FleetResp {
  agentFleet: AstroliftAgentListItem[];
}
interface LiveResp {
  agentLiveStatus: AstroliftAgentLiveStatus[];
}
interface SpecsResp {
  agentEnvironmentSpecs: AstroliftAgentEnvironmentSpec[];
}
interface EnvsResp {
  astroliftEnvironments: { registeredAppSlug: string; clusterSlug?: string | null }[];
}
interface MyAppsResp {
  astroliftMyAppsPage: { items: { slug: string }[]; nextCursor?: string | null };
}
interface RunAgentResp {
  runAstroliftAgent: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { id: string } | null;
  };
}

/** The backend's page cap: up to this many of the viewer's apps back Mine. */
const MY_APPS_LIMIT = 200;

/**
 * The Agents list: URL list state, the org's fleet joined with live status
 * (polled every 15s, as the registry did), environment specs (model and
 * runtime) and environments (cluster), plus the viewer's apps while Mine is
 * open. Each is one org-wide query the agent frame and the dispatch page read
 * too, so Apollo serves them once. Run now fires `runAstroliftAgent` and
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
  const mine = state.view === "mine";

  const fleet = useQuery<FleetResp>(LIST_AGENT_FLEET, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });
  const live = useQuery<LiveResp>(LIST_AGENT_LIVE_STATUS, {
    variables: { orgId, projectSlug: null, workloadId: null },
    skip: !orgId,
    pollInterval: 15000,
    fetchPolicy: "cache-and-network",
  });
  const specs = useQuery<SpecsResp>(LIST_AGENT_ENVIRONMENT_SPECS, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });
  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, { variables: { appSlug: null } });
  const myApps = useQuery<MyAppsResp>(LIST_MY_APPS_PAGE, {
    variables: { limit: MY_APPS_LIMIT },
    skip: !mine,
  });

  // Model, runtime, cluster and Mine are best effort: when a side query
  // fails the list still renders and those columns read "unknown".
  const agents: AgentRow[] = React.useMemo(
    () =>
      joinAgents(fleet.data?.agentFleet ?? [], {
        live: live.data?.agentLiveStatus ?? [],
        specs: specs.data?.agentEnvironmentSpecs ?? [],
        environments: envs.data?.astroliftEnvironments ?? [],
        myAppSlugs: new Set((myApps.data?.astroliftMyAppsPage.items ?? []).map((a) => a.slug)),
      }),
    [fleet.data, live.data, specs.data, envs.data, myApps.data]
  );

  const { rows, totalCount } = selectAgents(agents, {
    q: state.q,
    filters: list.filters,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

  const [runAgent, { loading: dispatching }] = useMutation<RunAgentResp>(RUN_AGENT);

  async function onRun(agent: Pick<AgentRow, "slug" | "name">): Promise<boolean> {
    try {
      const { data } = await runAgent({ variables: { input: { agentSlug: agent.slug } } });
      const result = data?.runAstroliftAgent;
      if (!result?.ok) throw new Error(result?.errors?.[0]?.message ?? "Dispatch failed");
      toast.success(`Dispatched ${agent.name}`);
      await client.refetchQueries({ include: [LIST_AGENT_TASKS, LIST_AGENT_FLEET] });
      router.push(`/agents/${encodeURIComponent(agent.slug)}/runs`);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(`Couldn't dispatch ${agent.name}`, { description: message });
      return false;
    }
  }

  const waiting =
    !orgId || (fleet.loading && !fleet.data) || (mine && myApps.loading && !myApps.data);

  return {
    list,
    rows,
    totalCount,
    loading: waiting,
    // Rows answer an older poll or the Mine join is still arriving.
    stale: fleet.loading && Boolean(fleet.data),
    error: fleet.error && !fleet.data ? { message: fleet.error.message } : null,
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
