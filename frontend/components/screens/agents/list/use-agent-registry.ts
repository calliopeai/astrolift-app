"use client";

import { useQuery } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import {
  LIST_AGENT_FLEET,
  LIST_AGENT_LIVE_STATUS,
  LIST_AGENT_WORKLOADS,
} from "@/graphql/agents/agents.queries";
import type {
  AstroliftAgentListItem,
  AstroliftAgentLiveStatus,
} from "@/graphql/agents/agents.types";
import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import { useModules } from "@/graphql/user/user.hooks";

interface AgentListResp {
  agentWorkloads: AstroliftAgentListItem[];
}
interface AgentFleetResp {
  agentFleet: AstroliftAgentListItem[];
}
interface AgentLiveStatusResp {
  agentLiveStatus: AstroliftAgentLiveStatus[];
}

export interface RegistryProject {
  id: string;
  slug: string;
  name: string;
}

interface ProjectsResp {
  astroliftProjects: RegistryProject[];
}

/**
 * Registry tab — registered AGENTS (kind=agent Workloads), project-scoped
 * with a fleet/all-agents toggle. (spec 33 §3 / PR-7)
 *
 * This is the *registered-agents* surface, not the runs surface — runs
 * live in the Active / History tabs. The base list (agentWorkloads /
 * agentFleet) is fetched once; the volatile live-status (running count,
 * next-scheduled, paused/idle) is a separate 15s-polled query merged into
 * rows by workloadId, matching how /jobs polls its run signal. The data
 * half of AgentRegistryPanel.
 */
export function useAgentRegistry(orgId: string) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // The "Register an agent repo" create affordance is gated on the Agents
  // module's server-authoritative `canCreate` (spec 36 §1.3).
  const { canCreate } = useModules();
  const canCreateAgent = canCreate("agents");

  // Project scoping is URL-synced via ?project= (mirrors the Apps list).
  // Empty string === the fleet view (all agents in the org).
  const projectSlug = searchParams.get("project") ?? "";
  const fleet = projectSlug === "";

  const setProjectScope = React.useCallback(
    (next: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (!next) params.delete("project");
      else params.set("project", next);
      const qs = params.toString();
      router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });
    },
    [pathname, router, searchParams]
  );

  // Projects populate the scope picker. Cheap query; cached across the app.
  const { data: projectsData } = useQuery<ProjectsResp>(LIST_PROJECTS);
  const projects = projectsData?.astroliftProjects ?? [];

  // Base list: fleet vs project-scoped. We run one query and skip the other
  // so we never over-fetch. cache-and-network keeps the list fresh on
  // re-entry without flashing the skeleton.
  const projectQuery = useQuery<AgentListResp>(LIST_AGENT_WORKLOADS, {
    variables: { orgId, projectSlug },
    skip: !orgId || fleet,
    fetchPolicy: "cache-and-network",
  });
  const fleetQuery = useQuery<AgentFleetResp>(LIST_AGENT_FLEET, {
    variables: { orgId },
    skip: !orgId || !fleet,
    fetchPolicy: "cache-and-network",
  });

  const agents: AstroliftAgentListItem[] = React.useMemo(
    () => (fleet ? (fleetQuery.data?.agentFleet ?? []) : (projectQuery.data?.agentWorkloads ?? [])),
    [fleet, fleetQuery.data?.agentFleet, projectQuery.data?.agentWorkloads]
  );
  const loading = fleet ? fleetQuery.loading : projectQuery.loading;

  // Volatile live-status, polled every 15s (matching /jobs). Scoped the same
  // way as the list; merged into rows by workloadId. No workloadId arg ⇒ the
  // whole scope rolls up in one request.
  const { data: liveData } = useQuery<AgentLiveStatusResp>(LIST_AGENT_LIVE_STATUS, {
    variables: { orgId, projectSlug: fleet ? null : projectSlug, workloadId: null },
    skip: !orgId,
    pollInterval: 15000,
    fetchPolicy: "cache-and-network",
  });
  const liveByWorkloadId = React.useMemo(() => {
    const map = new Map<string, AstroliftAgentLiveStatus>();
    for (const row of liveData?.agentLiveStatus ?? []) {
      map.set(row.workloadId, row);
    }
    return map;
  }, [liveData?.agentLiveStatus]);

  return {
    canCreateAgent,
    projectSlug,
    fleet,
    setProjectScope,
    projects,
    agents,
    loading,
    liveByWorkloadId,
  };
}
