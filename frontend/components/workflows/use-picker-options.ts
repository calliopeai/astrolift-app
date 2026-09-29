"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_AGENT_WORKLOADS, LIST_SKILLS } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentListItem, AstroliftSkill } from "@/graphql/agents/agents.types";

import type { SkillOption } from "./pickers";

interface AgentWorkloadsResp {
  agentWorkloads: AstroliftAgentListItem[];
}

interface SkillsResp {
  skills: AstroliftSkill[];
}

/** The org's agent workloads for AgentWorkloadPicker; skipped until the org resolves. */
export function useAgentWorkloadOptions(orgId: string | null) {
  const { data, loading } = useQuery<AgentWorkloadsResp>(LIST_AGENT_WORKLOADS, {
    variables: { orgId },
    fetchPolicy: "cache-and-network",
    skip: !orgId,
  });

  const workloads = React.useMemo(() => {
    const rows = data?.agentWorkloads ?? [];
    return [...rows].sort((a, b) => a.name.localeCompare(b.name));
  }, [data?.agentWorkloads]);

  return { workloads, loading };
}

/** Org skills union platform-global skills for SkillRefsPicker, deduped by slug (org wins). */
export function useSkillOptions(orgId: string | null) {
  const orgSkills = useQuery<SkillsResp>(LIST_SKILLS, {
    variables: { orgId, isGlobal: false },
    fetchPolicy: "cache-and-network",
    skip: !orgId,
  });
  const globalSkills = useQuery<SkillsResp>(LIST_SKILLS, {
    variables: { orgId, isGlobal: true },
    fetchPolicy: "cache-and-network",
    skip: !orgId,
  });

  const options = React.useMemo<SkillOption[]>(() => {
    const bySlug = new Map<string, SkillOption>();
    for (const s of globalSkills.data?.skills ?? []) {
      bySlug.set(s.slug, { slug: s.slug, name: s.name, isGlobal: s.isGlobal });
    }
    for (const s of orgSkills.data?.skills ?? []) {
      bySlug.set(s.slug, { slug: s.slug, name: s.name, isGlobal: s.isGlobal });
    }
    return [...bySlug.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [orgSkills.data?.skills, globalSkills.data?.skills]);

  return { options, loading: orgSkills.loading || globalSkills.loading };
}
