"use client";

import { useCallback, useEffect } from "react";
import { useQuery } from "@apollo/client/react";
import { useListState } from "@/components/list/use-list-state";
import type { ListSecretChangeProposalsPageQuery } from "@/graphql/__generated__/operations";
import { LIST_SECRET_CHANGE_PROPOSALS_PAGE } from "@/graphql/services/services.queries";
import { SECRET_PROPOSALS_CHANGED } from "./secret-proposal-queue-events";
import { SECRET_PROPOSALS_LIST } from "./secret-proposals-list";

export type SecretProposalMetadata =
  ListSecretChangeProposalsPageQuery["astroliftSecretChangeProposalsPage"]["items"][number];

export function useSecretProposalsQueue() {
  const list = useListState(SECRET_PROPOSALS_LIST);
  const { state, setPageSize } = list;
  const { data, previousData, loading, error, refetch } =
    useQuery<ListSecretChangeProposalsPageQuery>(LIST_SECRET_CHANGE_PROPOSALS_PAGE, {
      variables: { appSlug: null, status: "pending", limit: state.pageSize, after: state.after },
      fetchPolicy: "no-cache",
      pollInterval: 30_000,
      notifyOnNetworkStatusChange: true,
    });
  const page = (data ?? previousData)?.astroliftSecretChangeProposalsPage;
  const onRetry = useCallback(() => {
    setPageSize(state.pageSize);
    void refetch({ after: null }).catch(() => {});
  }, [setPageSize, state.pageSize, refetch]);
  useEffect(() => {
    window.addEventListener(SECRET_PROPOSALS_CHANGED, onRetry);
    return () => window.removeEventListener(SECRET_PROPOSALS_CHANGED, onRetry);
  }, [onRetry]);
  return {
    list,
    rows: page?.items ?? [],
    totalCount: error ? null : (page?.totalCount ?? null),
    nextCursor: error ? null : (page?.nextCursor ?? null),
    loading: loading && !page,
    stale: loading && !!page,
    error: error ?? null,
    onRetry,
    onRefresh: onRetry,
  };
}
