"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_TOOL_DEFS } from "@/graphql/agents/agents.queries";

export type ToolDef = {
  id: string;
  name: string;
  slug: string;
  description: string;
  adapter: string;
  handlerRef: string;
  inputSchema: unknown;
  outputSchema: unknown;
  createdAt: string;
};

type ToolDefsData = { toolDefs: ToolDef[] };

/**
 * A skill's tool definitions. The Builder's summary and the Tools tab read
 * the same query, so the second one to mount answers from the cache (Leo's
 * page rule 2).
 */
export function useSkillToolDefs(skillId: string) {
  const { data, previousData, loading, error, refetch } = useQuery<ToolDefsData>(LIST_TOOL_DEFS, {
    variables: { skillId },
    fetchPolicy: "cache-and-network",
    skip: !skillId,
  });
  const tools = (data ?? previousData)?.toolDefs ?? [];
  return {
    tools,
    loading: loading && tools.length === 0 && !error,
    error: error ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
  };
}
