"use client";

import { useQuery } from "@apollo/client/react";
import { useState } from "react";

import {
  CLUSTER_PROMETHEUS_METRICS,
  CLUSTER_PROMETHEUS_RANGE_METRICS,
} from "@/graphql/clusters/clusters.queries";

import { type PrometheusInstant, type PrometheusRange, WINDOWS, type WindowLabel } from "./types";

interface PrometheusRangeResp {
  astroliftClusterPrometheusRangeMetrics: PrometheusRange;
}

interface PrometheusInstantResp {
  astroliftClusterPrometheusMetrics: PrometheusInstant;
}

/** Prometheus saturation: the instant KPIs and the range series for one window. */
export function useClusterMetrics(clusterId: string) {
  const [selectedWindow, setSelectedWindow] = useState<WindowLabel>("1h");
  const win = WINDOWS.find((w) => w.label === selectedWindow)!;

  const { data: rangeData, loading: rangeLoading } = useQuery<PrometheusRangeResp>(
    CLUSTER_PROMETHEUS_RANGE_METRICS,
    {
      variables: {
        clusterId,
        rangeSeconds: win.rangeSeconds,
        stepSeconds: win.stepSeconds,
      },
      pollInterval: 60000,
    }
  );

  const { data: instantData, loading: instantLoading } = useQuery<PrometheusInstantResp>(
    CLUSTER_PROMETHEUS_METRICS,
    {
      variables: { clusterId },
      pollInterval: 60000,
    }
  );

  const range = rangeData?.astroliftClusterPrometheusRangeMetrics ?? null;
  const instant = instantData?.astroliftClusterPrometheusMetrics ?? null;

  return {
    selectedWindow,
    onWindowChange: setSelectedWindow,
    range,
    rangeLoading: rangeLoading && !range,
    instant,
    instantLoading: instantLoading && !instant,
  };
}
