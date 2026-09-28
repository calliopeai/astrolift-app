"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_SKILLS } from "@/graphql/agents/agents.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

export type SkillListItem = {
  id: string;
  name: string;
  slug: string;
  description: string;
  skillVersion: number;
  isGlobal: boolean;
  isActive: boolean;
  agentType?: string;
};

type SkillsData = {
  skills: SkillListItem[];
};

/** The org's skill registry, split into own and global skills. The data half of SkillsListScreen. */
export function useSkillsList() {
  // Reactive org id (#agents-empty): a synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" and the query skipped.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";

  const { data, loading, error } = useQuery<SkillsData>(LIST_SKILLS, {
    variables: { orgId },
    fetchPolicy: "cache-and-network",
    skip: !orgId,
  });

  const skills = data?.skills ?? [];
  return {
    loading,
    errorMessage: error ? error.message : null,
    skills,
    ownSkills: skills.filter((s) => !s.isGlobal),
    globalSkills: skills.filter((s) => s.isGlobal),
  };
}

export type SkillsListState = ReturnType<typeof useSkillsList>;
