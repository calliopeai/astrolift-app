"use client";
import { useEffect } from "react";
import { useQuery } from "@apollo/client/react";
import { LIST_SECRET_CHANGE_PROPOSALS_PAGE } from "@/graphql/services/services.queries";
import type { ListSecretChangeProposalsPageQuery } from "@/graphql/__generated__/operations";
import { SECRET_PROPOSALS_CHANGED } from "./secret-proposal-queue-events";

export function useSecretProposalsSummary() {
  const { data, loading, error, refetch } = useQuery<ListSecretChangeProposalsPageQuery>(
    LIST_SECRET_CHANGE_PROPOSALS_PAGE,
    {
      variables: { appSlug: null, status: "pending", limit: 5, after: null },
      fetchPolicy: "no-cache",
      pollInterval: 30_000,
    }
  );
  useEffect(() => {
    const refresh = () => {
      void refetch().catch(() => {});
    };
    window.addEventListener(SECRET_PROPOSALS_CHANGED, refresh);
    return () => window.removeEventListener(SECRET_PROPOSALS_CHANGED, refresh);
  }, [refetch]);
  return {
    rows: data?.astroliftSecretChangeProposalsPage.items ?? [],
    count: error ? null : (data?.astroliftSecretChangeProposalsPage.totalCount ?? null),
    loading: loading && !data,
    error: error ?? null,
    onRetry: () => {
      void refetch().catch(() => {});
    },
  };
}
