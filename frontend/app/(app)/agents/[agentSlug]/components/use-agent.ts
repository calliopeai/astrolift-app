"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_AGENT_FLEET } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";
import { getActiveOrgGuid } from "@/lib/identity/active-org";

interface AgentFleetResp {
  agentFleet: AstroliftAgentListItem[];
}

export interface UseAgentResult {
  /** The matched agent row, or null while loading / when not found. */
  agent: AstroliftAgentListItem | null;
  loading: boolean;
  /** True once the fleet has loaded and no row matched the slug. */
  notFound: boolean;
  orgId: string;
}

/**
 * Resolve `[agentSlug]` to a single registered agent.
 *
 * There is no `agent(slug)` query in the schema yet (see spec 33 PR-9 — the
 * `agent(slug)` join is the net-new backend work, filed under PR-2 if
 * missing), so we resolve against the org-wide fleet list (`agentFleet`) and
 * match on the workload `slug`. The fleet row is the only existing query that
 * returns agents keyed by slug without already knowing the owning `appSlug`;
 * once matched it carries the `appSlug` the Build tab needs to drive
 * `GET_WORKLOAD` / `LIST_CONTAINERS` / `GET_WORKLOAD_MANIFEST`.
 *
 * The agent's brief / attached skills / tools are NOT resolvable from
 * existing queries (they have no agent→brief / agent→skill join) — that's the
 * backend gap the Build tab surfaces explicitly rather than faking.
 */
export function useAgent(agentSlug: string): UseAgentResult {
  const orgId = getActiveOrgGuid() ?? "";
  const { data, loading } = useQuery<AgentFleetResp>(LIST_AGENT_FLEET, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });

  const agent = React.useMemo(() => {
    const rows = data?.agentFleet ?? [];
    return rows.find((a) => a.slug === agentSlug) ?? null;
  }, [data?.agentFleet, agentSlug]);

  const fleetLoaded = Boolean(data?.agentFleet);

  return {
    agent,
    loading: loading && !fleetLoaded,
    notFound: fleetLoaded && agent === null,
    orgId,
  };
}
