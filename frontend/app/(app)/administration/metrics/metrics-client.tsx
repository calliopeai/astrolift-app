"use client";

import { AdminMetricsScreen } from "@/components/screens/administration/insights/AdminMetricsScreen";
import { useAdminMetrics } from "@/components/screens/administration/insights/use-admin-metrics";

import { ClusterLivePanels } from "./_components/cluster-live-panels";

export function AdminMetricsClient() {
  return (
    <AdminMetricsScreen
      {...useAdminMetrics()}
      renderLivePanels={(cluster, win) => <ClusterLivePanels cluster={cluster} win={win} />}
    />
  );
}
