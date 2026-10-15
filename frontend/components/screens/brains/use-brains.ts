"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { useListState } from "@/components/list/use-list-state";
import { useLocalizedList } from "@/components/list/use-localized-list";
import type {
  IssueBrainReadTokenMutation,
  IssueBrainReadTokenMutationVariables,
  ListBrainsQuery,
} from "@/graphql/__generated__/operations";
import { ISSUE_BRAIN_READ_TOKEN } from "@/graphql/registry/brains.mutations";
import { LIST_BRAINS } from "@/graphql/registry/brains.queries";

import type { BrainConnection } from "./brain-connection";
import { BRAINS_LIST } from "./brains-list";

export type BrainRow = ListBrainsQuery["astroliftBrains"][number];

/**
 * The data half of BrainsScreen: the server's search over readable brains,
 * and Connect, which issues the caller a read token for one of them. The
 * token lives in this state only until the dialog is dismissed.
 */
export function useBrains() {
  const t = useTranslations("brains");
  const list = useListState(useLocalizedList(BRAINS_LIST));
  const search = list.state.q.trim();
  const { data, previousData, loading, error, refetch } = useQuery<ListBrainsQuery>(LIST_BRAINS, {
    variables: { search: search || null },
  });
  const rows = (data ?? previousData)?.astroliftBrains ?? [];
  const [connection, setConnection] = React.useState<BrainConnection | null>(null);
  // Kept out of the Apollo cache, so the token is held only by this state.
  const [issue, issueState] = useMutation<
    IssueBrainReadTokenMutation,
    IssueBrainReadTokenMutationVariables
  >(ISSUE_BRAIN_READ_TOKEN, { fetchPolicy: "no-cache" });
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
    connection,
    connecting: issueState.loading,
    onConnect: async (slug: string) => {
      const { data: issued } = await issue({ variables: { slug } }).catch(() => ({ data: null }));
      const result = issued?.issueBrainReadToken;
      if (result?.ok && result.data) setConnection(result.data);
      else toast.error(result?.errors[0]?.message ?? t("connectFailed"));
    },
    onDismissConnection: () => setConnection(null),
  };
}

export type BrainsState = ReturnType<typeof useBrains>;
