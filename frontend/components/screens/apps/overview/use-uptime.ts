"use client";

import { gql } from "@apollo/client";
import { useQuery } from "@apollo/client/react";

// Hand-typed (not codegen'd): the astroliftAppUptime query is answered by the
// deployed backend at runtime; mirrors the pipeline-detail gql pattern.
const GET_APP_UPTIME = gql`
  query GetAppUptime($appSlug: String!) {
    astroliftAppUptime(appSlug: $appSlug) {
      isUp
      lastCheckedAt
      uptimePct
      totalChecks
      windowHours
      recent {
        checkedAt
        isUp
        latencyMs
        statusCode
      }
    }
  }
`;

export interface UptimePoint {
  checkedAt: string;
  isUp: boolean;
  latencyMs: number;
  statusCode: number | null;
}
export interface AppUptime {
  isUp: boolean | null;
  lastCheckedAt: string | null;
  uptimePct: number;
  totalChecks: number;
  windowHours: number;
  recent: UptimePoint[];
}
interface Resp {
  astroliftAppUptime: AppUptime | null;
}

/** Data half of UptimeCardView: the app's uptime probe summary, polled every minute. */
export function useUptime(appSlug: string) {
  const { data, loading, error, refetch } = useQuery<Resp>(GET_APP_UPTIME, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
    pollInterval: 60000,
  });
  return {
    uptime: data?.astroliftAppUptime ?? null,
    loading,
    /** Only when there is nothing cached to show. */
    error: data ? null : (error?.message ?? null),
    onRetry: () => void refetch(),
  };
}
