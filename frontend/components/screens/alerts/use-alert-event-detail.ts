"use client";

import { useQuery } from "@apollo/client/react";

import { GET_ALERT_EVENT } from "@/graphql/operations/alerts.queries";

import type { AlertEvent } from "./use-alerts";

interface Resp {
  astroliftAlertEvent: AlertEvent | null;
}

/** Direct owner-filtered detail read, independent of the list window. */
export function useAlertEventDetail(id: string) {
  const { data, loading, error, refetch } = useQuery<Resp>(GET_ALERT_EVENT, {
    variables: { id },
    fetchPolicy: "cache-and-network",
  });

  const event = data?.astroliftAlertEvent ?? null;

  return {
    id,
    event,
    loading: loading && !data,
    error: data ? null : error?.message,
    onRetry: () => {
      void refetch().catch(() => {});
    },
  };
}
