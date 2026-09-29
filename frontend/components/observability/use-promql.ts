"use client";

import { useLazyQuery, useQuery } from "@apollo/client/react";
import * as React from "react";

import type { AstroliftExecutePromqlResult } from "@/graphql/__generated__/schema";
import { APP_METRIC_NAMES, EXECUTE_PROMQL } from "@/graphql/observability/observability.queries";

import { type MetricDiscovery, type PromqlRangeKey, RANGE_OPTIONS } from "./PromqlQueryPanel";

/**
 * Ad-hoc PromQL (#647) and the app's own metric names (#1226), fetched only
 * once the panel is opened: a Prometheus round trip for a panel collapsed
 * by default. The data half of PromqlQueryPanel.
 */
export function usePromql(appSlug: string, environmentName?: string | null) {
  const [open, setOpen] = React.useState(false);
  const [execute, { data, loading, error }] = useLazyQuery<{
    astroliftExecutePromql: AstroliftExecutePromqlResult;
  }>(EXECUTE_PROMQL, { fetchPolicy: "network-only" });
  const names = useQuery<{ astroliftAppMetricNames: MetricDiscovery }>(APP_METRIC_NAMES, {
    variables: { appSlug, environmentName: environmentName ?? null, limit: 200 },
    skip: !open,
    fetchPolicy: "cache-first",
  });

  function onRun(query: string, range: PromqlRangeKey) {
    const opt = RANGE_OPTIONS.find((o) => o.key === range) ?? RANGE_OPTIONS[1];
    const endUnix = Math.floor(Date.now() / 1000);
    void execute({
      variables: {
        appSlug,
        query,
        startUnix: endUnix - opt.seconds,
        endUnix,
        stepSeconds: opt.step,
        environmentName: environmentName ?? null,
      },
    });
  }

  return {
    open,
    onOpenChange: setOpen,
    discovery: names.data?.astroliftAppMetricNames ?? null,
    discoveryLoading: names.loading,
    running: loading,
    transportError: error?.message ?? null,
    result: (data?.astroliftExecutePromql as AstroliftExecutePromqlResult | undefined) ?? null,
    onRun,
  };
}
