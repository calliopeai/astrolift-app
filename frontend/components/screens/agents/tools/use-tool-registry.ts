"use client";

import { useQuery } from "@apollo/client/react";

import { useListState } from "@/components/list/use-list-state";
import { LIST_ORG_TOOL_DEFS } from "@/graphql/agents/agents.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { selectTools, TOOLS_LIST } from "./tools-list";

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
 * Every tool definition across every skill in the active org, on URL list
 * state. The data half of ToolRegistryScreen.
 */
export function useToolRegistry() {
  const list = useListState(TOOLS_LIST);
  const { state } = list;
  // Reactive org id (#agents-empty): a synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" and the query skipped.
  const { org, loading: orgLoading } = useActiveOrg();
  const orgId = org?.id ?? "";

  const { data, previousData, loading, error, refetch } = useQuery<OrgToolDefsData>(
    LIST_ORG_TOOL_DEFS,
    { variables: { orgId }, fetchPolicy: "cache-and-network", skip: !orgId }
  );
  const tools = (data ?? previousData)?.orgToolDefs ?? [];
  const { rows, totalCount } = selectTools(tools, {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

  return {
    list,
    rows,
    totalCount,
    loading: (loading || orgLoading) && tools.length === 0 && !error,
    error: error ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
  };
}

export type ToolRegistryState = ReturnType<typeof useToolRegistry>;
