"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_SECRET_CHANGE_PROPOSALS } from "@/graphql/services/services.queries";
import type { AstroliftSecretChangeProposal } from "@/graphql/services/services.types";

interface ListResp {
  astroliftSecretChangeProposals: AstroliftSecretChangeProposal[];
}

/**
 * Pending secret-change proposals across the org (#488), polled every 30s.
 * The data half of SecretProposalsQueue. `loading` is true only before the
 * first response, matching the queue's skeleton rule.
 */
export function useSecretProposalsQueue() {
  const { data, loading } = useQuery<ListResp>(LIST_SECRET_CHANGE_PROPOSALS, {
    variables: { appSlug: null, status: "pending" },
    fetchPolicy: "cache-and-network",
    pollInterval: 30_000,
  });

  return {
    proposals: data?.astroliftSecretChangeProposals ?? [],
    loading: loading && !data,
  };
}
