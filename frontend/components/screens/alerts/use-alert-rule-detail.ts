"use client";

import { useQuery } from "@apollo/client/react";

import { GET_ALERT_RULE } from "@/graphql/operations/alerts.queries";

import type { AlertRule } from "./use-alerts";

export type AlertRuleDetailRow = AlertRule & { managedServiceId?: string | null };

interface Resp {
  astroliftAlertRule: AlertRuleDetailRow | null;
}

/** Direct owner-filtered detail read, independent of the list window. */
export function useAlertRuleDetail(id: string) {
  const { data, loading, error, refetch } = useQuery<Resp>(GET_ALERT_RULE, {
    variables: { id },
    fetchPolicy: "cache-and-network",
  });

  const rule = data?.astroliftAlertRule ?? null;

  return {
    id,
    rule,
    loading: loading && !data,
    error: data ? null : error?.message,
    onRetry: () => {
      void refetch().catch(() => {});
    },
  };
}
