"use client";

import {
  PrometheusPanel,
  SystemMetricsPanel,
} from "@/components/screens/administration/insights/ClusterMetricsPanels";
import type {
  MetricsCluster,
  MetricsWindow,
} from "@/components/screens/administration/insights/metrics-format";
import {
  usePrometheusPanel,
  useSystemMetricsPanel,
} from "@/components/screens/administration/insights/use-admin-metrics";

function PrometheusPanelContainer({
  cluster,
  win,
}: {
  cluster: MetricsCluster;
  win: MetricsWindow;
}) {
  return <PrometheusPanel {...usePrometheusPanel(cluster.id, cluster.slug, win)} />;
}

function SystemMetricsPanelContainer({
  cluster,
  win,
}: {
  cluster: MetricsCluster;
  win: MetricsWindow;
}) {
  return (
    <SystemMetricsPanel {...useSystemMetricsPanel(cluster.id, cluster.providerPluginSlug, win)} />
  );
}

/**
 * One live cluster's metric panels, wired to their queries. Mounted by the
 * metrics screen only for a cluster whose heartbeat says it is reachable.
 */
export function ClusterLivePanels({
  cluster,
  win,
}: {
  cluster: MetricsCluster;
  win: MetricsWindow;
}) {
  return (
    <>
      <PrometheusPanelContainer cluster={cluster} win={win} />
      <SystemMetricsPanelContainer cluster={cluster} win={win} />
    </>
  );
}
