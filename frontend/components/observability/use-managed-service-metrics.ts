"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import type { AstroliftManagedServiceMetrics } from "@/graphql/__generated__/schema";
import { GET_MANAGED_SERVICE_METRICS } from "@/graphql/observability/observability.queries";

import { TIME_RANGE_OPTIONS, type TimeRangeKey } from "./golden-signals-types";

interface MetricsResp {
  astroliftAppManagedServiceMetrics: AstroliftManagedServiceMetrics | null;
}

/** One managed service's metric series over a time range (#645 / #646). */
export function useManagedServiceMetrics(managedServiceId: string) {
  const [range, setRange] = React.useState<TimeRangeKey>("1h");
  const rangeSeconds = TIME_RANGE_OPTIONS.find((o) => o.key === range)?.seconds ?? 3600;

  const q = useQuery<MetricsResp>(GET_MANAGED_SERVICE_METRICS, {
    variables: { managedServiceId, rangeSeconds },
    fetchPolicy: "cache-and-network",
  });

  return {
    range,
    onRangeChange: setRange,
    data: q.data?.astroliftAppManagedServiceMetrics ?? null,
    loading: q.loading && !q.data,
  };
}
