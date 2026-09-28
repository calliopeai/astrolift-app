"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_ORG_TOOL_DEFS } from "@/graphql/agents/agents.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

export type ToolRegistryTool = {
  id: string;
  name: string;
  slug: string;
  description: string;
  adapter: string;
  handlerRef: string;
  createdAt: string;
};

type OrgToolDefsData = { orgToolDefs: ToolRegistryTool[] };

/**
 * Every tool definition across every skill in the active org. The data half
 * of ToolRegistryScreen.
 */
export function useToolRegistry() {
  // Reactive org id (#agents-empty): a synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" and the query skipped.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";

  const { data, loading, error } = useQuery<OrgToolDefsData>(LIST_ORG_TOOL_DEFS, {
    variables: { orgId },
    fetchPolicy: "cache-and-network",
    skip: !orgId,
  });

  return {
    tools: data?.orgToolDefs ?? [],
    loading,
    error: error ? { message: error.message } : null,
  };
}
