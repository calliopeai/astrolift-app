"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import {
  GET_DEPLOYMENT_METRICS,
  LIST_APP_HEALTH_SUMMARY,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppHealthSummary,
  AstroliftDeploymentMetrics,
} from "@/graphql/lifecycle/lifecycle.types";

interface MetricsResp {
  astroliftDeploymentMetrics: AstroliftDeploymentMetrics;
}
interface HealthResp {
  astroliftAppHealthSummary: AstroliftAppHealthSummary[];
}

/** Deployment rollups over the window and the per-app health summary. */
export function useMetrics() {
  const [windowDays] = React.useState(30);

  const { data: metricsData, loading: metricsLoading } = useQuery<MetricsResp>(
    GET_DEPLOYMENT_METRICS,
    { variables: { windowDays }, pollInterval: 30000 }
  );
  const { data: healthData, loading: healthLoading } = useQuery<HealthResp>(
    LIST_APP_HEALTH_SUMMARY,
    { pollInterval: 30000 }
  );

  return {
    windowDays,
    metrics: metricsData?.astroliftDeploymentMetrics,
    metricsLoading,
    apps: healthData?.astroliftAppHealthSummary ?? [],
    healthLoading,
  };
}
