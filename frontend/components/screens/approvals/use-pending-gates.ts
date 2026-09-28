"use client";

import { usePendingHumanGates } from "@/graphql/workflows/tiered.hooks";

// Freshness matches the approvals queue (#420): frequent enough that a
// newly-opened gate shows up without a manual refresh, not so tight it
// hammers the server for a list that changes on human timescales.
const POLL_MS = 30_000;

/** Org-wide open human gates (#1820). The data half of PendingGatesScreen. */
export function usePendingGates() {
  const { gates, loading, error, refetch } = usePendingHumanGates({ pollInterval: POLL_MS });
  return {
    gates,
    loading,
    error: error?.message ?? null,
    onRefresh: () => {
      void refetch();
    },
  };
}
