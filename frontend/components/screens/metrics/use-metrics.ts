"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { useListState } from "@/components/list/use-list-state";

import {
  GET_DEPLOYMENT_METRICS,
  LIST_APP_HEALTH_SUMMARY,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppHealthSummary,
  AstroliftDeploymentMetrics,
} from "@/graphql/lifecycle/lifecycle.types";

import { METRICS_APPS_LIST } from "./metrics-apps-list";

interface MetricsResp {
  astroliftDeploymentMetrics: AstroliftDeploymentMetrics;
}
interface HealthResp {
  astroliftAppHealthSummary: AstroliftAppHealthSummary[];
}

/**
 * Deployment rollups over the window and the per-app health summary, with
 * the apps list's URL state (the screen pages the summary in the client).
 */
export function useMetrics() {
  const [windowDays] = React.useState(30);
  const list = useListState(METRICS_APPS_LIST);

  const metricsQuery = useQuery<MetricsResp>(GET_DEPLOYMENT_METRICS, {
    variables: { windowDays },
    pollInterval: 30000,
  });
  const healthQuery = useQuery<HealthResp>(LIST_APP_HEALTH_SUMMARY, { pollInterval: 30000 });

  return {
    list,
    windowDays,
    metrics: (metricsQuery.data ?? metricsQuery.previousData)?.astroliftDeploymentMetrics,
    metricsLoading: metricsQuery.loading,
    metricsError: metricsQuery.error?.message ?? null,
    onRetryMetrics: () => {
      void metricsQuery.refetch().catch(() => undefined);
    },
    apps: (healthQuery.data ?? healthQuery.previousData)?.astroliftAppHealthSummary ?? [],
    healthLoading: healthQuery.loading,
    healthError: healthQuery.error?.message ?? null,
    onRetryHealth: () => {
      void healthQuery.refetch().catch(() => undefined);
    },
  };
}
