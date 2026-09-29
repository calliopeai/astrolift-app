"use client";

import { useQuery } from "@apollo/client/react";

import { GET_SKILL } from "@/graphql/agents/agents.queries";

export type Skill = {
  id: string;
  name: string;
  slug: string;
  description: string;
  content: string;
  skillVersion: number;
  isGlobal: boolean;
  isActive: boolean;
};

type SkillData = { skill: Skill | null };

/**
 * One skill, for the frame every skill route draws (Builder, Tools, Register
 * tool). One query shape, so moving between the tabs reads the cache instead
 * of fetching the skill again (Leo's page rule 2).
 */
export function useSkill(id: string) {
  const { data, loading, error, refetch } = useQuery<SkillData>(GET_SKILL, {
    variables: { id },
    fetchPolicy: "cache-and-network",
    skip: !id,
  });
  return {
    skill: data?.skill ?? null,
    loading: loading && !data,
    error: error ? error.message : null,
    onRetry: () => {
      void refetch();
    },
  };
}
