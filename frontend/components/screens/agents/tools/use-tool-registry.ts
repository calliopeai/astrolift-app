"use client";

import { useQuery } from "@apollo/client/react";

import { useListState } from "@/components/list/use-list-state";
import { TOOL_DEFS_LIST_PAGE } from "@/graphql/agents/agents.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { TOOLS_LIST, toolDefsPageVariables } from "./tools-list";

export type ToolRegistryTool = {
  id: string;
  name: string;
  slug: string;
  description: string;
  adapter: string;
  handlerRef: string;
  createdAt: string;
  isBuiltin: boolean;
  /** The skill the tool is registered on. */
  skillId?: string | null;
  skillSlug: string;
  skillName: string;
  skillIsGlobal: boolean;
  createdByEmail: string;
  createdByMe: boolean;
};

type ToolDefsPageData = {
  orgToolDefsPage: {
    items: ToolRegistryTool[];
    totalCount: number;
    page: number;
    pageSize: number;
  };
};

/**
 * Every tool definition across every skill in the active org and the global
 * catalog: URL list state in, one numbered page of `orgToolDefsPage` out
 * (#2155). The server filters, searches, sorts and counts. The data half of
 * ToolRegistryScreen.
 */
export function useToolRegistry() {
  const list = useListState(TOOLS_LIST);
  const { state } = list;
  // Reactive org id (#agents-empty): a synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" and the query skipped.
  const { org, loading: orgLoading } = useActiveOrg();
  const orgId = org?.id ?? "";

  const { data, previousData, loading, error, refetch } = useQuery<ToolDefsPageData>(
    TOOL_DEFS_LIST_PAGE,
    {
      variables: {
        orgId,
        ...toolDefsPageVariables({
          q: state.q,
          filters: list.filters,
          sort: state.sort,
          page: state.page,
          pageSize: state.pageSize,
        }),
      },
      fetchPolicy: "cache-and-network",
      skip: !orgId,
    }
  );
  const shown = data ?? previousData;
  const rows = shown?.orgToolDefsPage.items ?? [];

  return {
    list,
    rows,
    totalCount: shown?.orgToolDefsPage.totalCount ?? rows.length,
    loading: (loading || orgLoading || !orgId) && !shown && !error,
    // Rows on screen answer the previous list state while the next loads.
    stale: loading && !data && Boolean(shown),
    error: error ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
  };
}

export type ToolRegistryState = ReturnType<typeof useToolRegistry>;
