"use client";

import { useQuery } from "@apollo/client/react";

import { GET_AGENT_DETAIL } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentDetail, AstroliftAgentListItem } from "@/graphql/agents/agents.types";
import { GET_WORKLOAD, LIST_CONTAINERS } from "@/graphql/registry/registry.queries";
import type { AstroliftContainer, AstroliftWorkload } from "@/graphql/registry/registry.types";

import type { AgentBuildScreenProps } from "./AgentBuildScreen";

interface WorkloadResp {
  astroliftWorkload: AstroliftWorkload | null;
}
interface ContainersResp {
  astroliftContainers: AstroliftContainer[];
}
interface AgentDetailResp {
  agent: AstroliftAgentDetail | null;
}

/**
 * Data for an agent's Build tab (spec 33 PR-9 / spec 38 Phase 4).
 *
 * Composed from two reads:
 *   - container image      ← `GET_WORKLOAD` + `LIST_CONTAINERS` (`imageRef`)
 *   - source / repo        ← the resolved fleet row (`sourceRepo`/`sourceUrl`)
 *   - brief / skills / tools ← `GET_AGENT_DETAIL` (the `agent(orgId, slug)` join)
 */
export function useAgentBuild(agent: AstroliftAgentListItem, orgId: string): AgentBuildScreenProps {
  const { data: wlData, loading: wlLoading } = useQuery<WorkloadResp>(GET_WORKLOAD, {
    variables: { appSlug: agent.appSlug, slug: agent.slug },
    fetchPolicy: "cache-and-network",
  });
  const { data: cData, loading: cLoading } = useQuery<ContainersResp>(LIST_CONTAINERS, {
    variables: { workloadSlug: agent.slug },
    fetchPolicy: "cache-and-network",
  });
  const {
    data: adData,
    loading: adLoading,
    error: adError,
  } = useQuery<AgentDetailResp>(GET_AGENT_DETAIL, {
    variables: { orgId, slug: agent.slug },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });

  const workload = wlData?.astroliftWorkload ?? null;
  const containers = cData?.astroliftContainers ?? [];
  const primary = containers.find((c) => c.isPrimary) ?? containers[0] ?? null;

  const detail = adData?.agent ?? null;
  const skills = [...(detail?.skills ?? [])].sort((a, b) => a.position - b.position);

  return {
    agent,
    workload,
    workloadLoading: wlLoading && !workload,
    primary,
    containersLoading: cLoading && containers.length === 0,
    detailLoading: adLoading && !detail,
    detailError: Boolean(adError),
    brief: detail?.brief ?? null,
    skills,
  };
}
