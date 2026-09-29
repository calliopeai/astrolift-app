"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { RUN_AGENT } from "@/graphql/agents/agents.mutations";
import {
  LIST_AGENT_ENVIRONMENT_SPECS,
  LIST_AGENT_FLEET,
  LIST_AGENT_TASKS,
} from "@/graphql/agents/agents.queries";
import type {
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
} from "@/graphql/agents/agents.types";

// The runAstroliftAgent envelope (id + status of the created AgentTask). Typed
// inline — the agents area hand-rolls its GraphQL response shapes rather than
// consuming codegen op-types (see graphql/agents/*.types.ts).
interface RunAgentResp {
  runAstroliftAgent: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { id: string; status: string; createdAt: string } | null;
  };
}
interface AgentFleetResp {
  agentFleet: AstroliftAgentListItem[];
}
interface EnvSpecsResp {
  agentEnvironmentSpecs: AstroliftAgentEnvironmentSpec[];
}

export interface DispatchInput {
  agent: AstroliftAgentListItem;
  triggerPayload: unknown | null;
  /** "" (Agent default) or a spec id. */
  environmentSpecId: string;
  timeoutSeconds: number | null;
}

/**
 * The org fleet + environment specs behind the Dispatch tab, and the
 * `runAstroliftAgent` Once dispatch. The data half of DispatchTabView.
 */
export function useDispatchTab(orgId: string) {
  // Pre-registered agents (the org fleet) populate the picker; the richer fleet
  // row (vs. raw workloads) carries each agent's configured cadence for the
  // context strip. Environment specs back the Advanced override.
  const { data: fleetData, loading: fleetLoading } = useQuery<AgentFleetResp>(LIST_AGENT_FLEET, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });
  const { data: envData } = useQuery<EnvSpecsResp>(LIST_AGENT_ENVIRONMENT_SPECS, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });
  const agents = React.useMemo(() => fleetData?.agentFleet ?? [], [fleetData?.agentFleet]);
  const envSpecs = envData?.agentEnvironmentSpecs ?? [];

  // Refetch the fleet task lists on success so the new run shows on the Active
  // tab without a reload (the query document is shared by Active + History).
  const [runAgent, { loading: dispatching }] = useMutation<RunAgentResp>(RUN_AGENT, {
    refetchQueries: [LIST_AGENT_TASKS],
  });

  /** Resolves the created run's id, or null when nothing to link to (or it failed). */
  async function onDispatch({
    agent,
    triggerPayload,
    environmentSpecId,
    timeoutSeconds,
  }: DispatchInput): Promise<{ id: string } | null> {
    const name = agent.name;
    try {
      const { data } = await runAgent({
        variables: {
          input: {
            agentSlug: agent.slug,
            triggerPayload,
            // "" (Agent default) → null; the resolver falls back to the
            // workload's own image/runtime when no spec is pinned.
            environmentSpecId: environmentSpecId || null,
            timeoutSeconds,
          },
        },
      });
      const result = data?.runAstroliftAgent;
      if (!result?.ok) {
        throw new Error(result?.errors?.[0]?.message ?? "Dispatch failed");
      }
      toast.success(`Dispatched ${name}`);
      return result.data ? { id: result.data.id } : null;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(`Couldn't dispatch ${name}`, { description: message });
      return null;
    }
  }

  return { agents, envSpecs, fleetLoading, dispatching, onDispatch };
}
