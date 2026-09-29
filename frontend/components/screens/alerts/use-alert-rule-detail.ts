"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_ALERT_RULES } from "@/graphql/operations/alerts.queries";

import type { AlertRule } from "./use-alerts";

export type AlertRuleDetailRow = AlertRule & { managedServiceId?: string | null };

interface Resp {
  astroliftAlertRules: AlertRuleDetailRow[];
}

/**
 * The data half of AlertRuleDetail (#1106). Reuses the global
 * LIST_ALERT_RULES window (no singular query exists) — a cache hit when
 * navigated from /alerts.
 */
export function useAlertRuleDetail(id: string) {
  const { data, loading } = useQuery<Resp>(LIST_ALERT_RULES, {
    variables: { activeOnly: false },
    fetchPolicy: "cache-and-network",
  });

  const rule = React.useMemo(
    () => (data?.astroliftAlertRules ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  return { id, rule, loading };
}
