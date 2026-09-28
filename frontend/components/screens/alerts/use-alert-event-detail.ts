"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_ALERT_EVENTS } from "@/graphql/operations/alerts.queries";

import type { AlertEvent } from "./use-alerts";

interface Resp {
  astroliftAlertEvents: AlertEvent[];
}

/**
 * The data half of AlertEventDetail (#1106). Reuses the global
 * LIST_ALERT_EVENTS window (no singular query exists).
 */
export function useAlertEventDetail(id: string) {
  const { data, loading } = useQuery<Resp>(LIST_ALERT_EVENTS, {
    variables: { unresolvedOnly: false, limit: 100 },
    fetchPolicy: "cache-and-network",
  });

  const event = React.useMemo(
    () => (data?.astroliftAlertEvents ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  return { id, event, loading };
}
