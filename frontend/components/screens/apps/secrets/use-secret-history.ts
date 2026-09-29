"use client";

import { useQuery } from "@apollo/client/react";

import { GET_APP_SECRET_HISTORY } from "@/graphql/services/services.queries";

import type { SecretHistoryEntry } from "./secrets.types";

interface SecretHistoryResp {
  astroliftAppSecretHistory: SecretHistoryEntry[];
}

/**
 * #714 — the per-key audit timeline. Runs only while the history popover is
 * open. The data half of SecretHistoryPanelView.
 */
export function useSecretHistory(appSlug: string, secretKey: string) {
  const { data, loading, error } = useQuery<SecretHistoryResp>(GET_APP_SECRET_HISTORY, {
    variables: { appSlug, key: secretKey },
    fetchPolicy: "cache-and-network",
  });
  return {
    secretKey,
    entries: data?.astroliftAppSecretHistory ?? [],
    loading,
    error: Boolean(error),
  };
}
