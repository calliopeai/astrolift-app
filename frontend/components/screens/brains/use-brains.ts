"use client";

import { useQuery } from "@apollo/client/react";

import { useListState } from "@/components/list/use-list-state";
import { useLocalizedList } from "@/components/list/use-localized-list";
import type { ListBrainsQuery } from "@/graphql/__generated__/operations";
import { LIST_BRAINS } from "@/graphql/registry/brains.queries";

import { BRAINS_LIST } from "./brains-list";

export type BrainRow = ListBrainsQuery["astroliftBrains"][number];

/** The data half of BrainsScreen: the server's search over readable brains. */
export function useBrains() {
  const list = useListState(useLocalizedList(BRAINS_LIST));
  const search = list.state.q.trim();
  const { data, previousData, loading, error, refetch } = useQuery<ListBrainsQuery>(LIST_BRAINS, {
    variables: { search: search || null },
  });
  const rows = (data ?? previousData)?.astroliftBrains ?? [];
  return {
    list,
    rows,
    totalCount: data ? rows.length : null,
    loading: loading && !data && !previousData,
    stale: loading && !!previousData,
    error: error ? { message: error.message } : null,
    onRetry: () => {
      void refetch().catch(() => {});
    },
  };
}

export type BrainsState = ReturnType<typeof useBrains>;
