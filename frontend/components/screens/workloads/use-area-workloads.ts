"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_WORKLOADS_PAGE } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import type { workloadsPageVariables } from "./workloads-list";

interface WorkloadsPageResp {
  astroliftWorkloadsPage: { items: AstroliftWorkload[]; totalCount: number | null };
}

/**
 * One numbered page of the Agents area's workloads (#2155): the Workloads
 * and Functions lists each build their variables from their list state
 * (`workloadsPageVariables`, `functionsPageVariables`) and read the page
 * here. The server filters, searches, sorts and counts.
 */
export function useAreaWorkloads(variables: ReturnType<typeof workloadsPageVariables>) {
  const query = useQuery<WorkloadsPageResp>(LIST_WORKLOADS_PAGE, {
    variables,
    fetchPolicy: "cache-and-network",
  });
  const data = query.data ?? query.previousData;
  const rows = data?.astroliftWorkloadsPage.items ?? [];
  return {
    rows,
    totalCount: data?.astroliftWorkloadsPage.totalCount ?? rows.length,
    loading: query.loading && !data && !query.error,
    // Rows on screen answer the previous list state while the next loads.
    stale: query.loading && !query.data && Boolean(data),
    error: query.error ? { message: query.error.message } : null,
    onRetry: () => {
      void query.refetch();
    },
  };
}
