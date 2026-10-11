"use client";

import { useQuery } from "@apollo/client/react";

import type { AccessPresetsQuery } from "@/graphql/__generated__/operations";
import { ACCESS_PRESETS } from "@/graphql/permissions/astrolift.queries";

export type AccessPreset = AccessPresetsQuery["astroliftAccessPresets"][number];

/** The server's Easy-mode presets (#2272); the data half of AccessPresetsView. */
export function useAccessPresets() {
  const query = useQuery<AccessPresetsQuery>(ACCESS_PRESETS, { fetchPolicy: "cache-and-network" });
  return {
    presets: query.data?.astroliftAccessPresets ?? null,
    loading: query.loading && !query.data,
    error: query.error?.message ?? null,
    onRetry: () => void query.refetch().catch(() => {}),
  };
}
