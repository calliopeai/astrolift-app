"use client";

import * as React from "react";

import { useQuery } from "@apollo/client/react";

import { SERVER_INFO } from "./server.queries";

// Public feature-flag keys surfaced by `astroliftServerInfo`
// (`core/schema/types/server_info.py::_PUBLIC_FEATURE_FLAGS`). Only the
// ones the UI actually gates on are named here.
export const FEATURE_FLAG_ZENTINELLE = "zentinelle.enabled";
export const FEATURE_FLAG_ADMIN_QUOTAS = "admin.quotas_enabled";

type ServerInfoData = {
  astroliftServerInfo: {
    featureFlags: { key: string; enabled: boolean }[];
  };
};

/**
 * Read a single public feature flag from the install handshake.
 *
 * The handshake is unauthenticated and install-global, so it is cached
 * (`cache-first`) — the flags don't change per-request. Until the query
 * resolves the flag reads `false`, which for Zentinelle gating means the
 * default (governance surfaces hidden) shows first and never flickers a
 * gated tab in before the server answer arrives.
 */
export function useFeatureFlag(key: string): boolean {
  const { data } = useQuery<ServerInfoData>(SERVER_INFO, {
    fetchPolicy: "cache-first",
  });

  return React.useMemo(
    () =>
      (data?.astroliftServerInfo?.featureFlags ?? []).some(
        (f) => f.key === key && f.enabled,
      ),
    [data?.astroliftServerInfo?.featureFlags, key],
  );
}
