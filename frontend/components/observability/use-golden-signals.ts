"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import type { ObservabilityPanelReason } from "@/components/observability/panel-reason";
import type {
  AstroliftAppGoldenSignal,
  AstroliftStatusCodeBreakdown,
} from "@/graphql/__generated__/schema";
import {
  GET_APP_GOLDEN_SIGNALS,
  GET_APP_STATUS_CODE_BREAKDOWN,
} from "@/graphql/observability/observability.queries";

import { DEFAULT_TIME_RANGE, TIME_RANGE_OPTIONS, type TimeRangeKey } from "./golden-signals-types";

interface GoldenSignalsResp {
  astroliftAppGoldenSignals: {
    reason: ObservabilityPanelReason;
    signals: AstroliftAppGoldenSignal[];
  };
}

export type StatusBreakdownWithReason = AstroliftStatusCodeBreakdown & {
  reason: ObservabilityPanelReason;
};

interface StatusBreakdownResp {
  astroliftAppStatusCodeBreakdown: StatusBreakdownWithReason | null;
}

/**
 * The golden signals and the status-code breakdown over one time range,
 * optionally scoped to an environment and a workload (#422). The data
 * half of GoldenSignalsPanel.
 */
export function useGoldenSignals(
  appSlug: string,
  environmentName?: string | null,
  workloadSlug?: string | null
) {
  const [range, setRange] = React.useState<TimeRangeKey>(DEFAULT_TIME_RANGE);
  const rangeSeconds =
    TIME_RANGE_OPTIONS.find((o) => o.key === range)?.seconds ?? TIME_RANGE_OPTIONS[1].seconds;
  const variables = {
    appSlug,
    environmentName: environmentName ?? null,
    workloadSlug: workloadSlug ?? null,
    rangeSeconds,
  };

  const signals = useQuery<GoldenSignalsResp>(GET_APP_GOLDEN_SIGNALS, {
    variables,
    fetchPolicy: "cache-and-network",
  });
  const statusBreakdown = useQuery<StatusBreakdownResp>(GET_APP_STATUS_CODE_BREAKDOWN, {
    variables,
    fetchPolicy: "cache-and-network",
  });

  return {
    range,
    onRangeChange: setRange,
    signals: signals.data?.astroliftAppGoldenSignals?.signals ?? null,
    reason: signals.data?.astroliftAppGoldenSignals?.reason ?? null,
    loading: signals.loading && !signals.data,
    onRetry: () => void signals.refetch(),
    statusBreakdown: statusBreakdown.data?.astroliftAppStatusCodeBreakdown ?? null,
    statusLoading: statusBreakdown.loading && !statusBreakdown.data,
    onStatusRetry: () => void statusBreakdown.refetch(),
  };
}
