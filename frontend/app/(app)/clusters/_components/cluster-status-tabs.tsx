"use client";

import { ClusterActivityBody } from "@/components/screens/clusters/status/ClusterActivityScreen";
import { ClusterHealthBody } from "@/components/screens/clusters/status/ClusterHealthScreen";
import {
  ClusterStatusBody,
  StatusLifecycleCard,
  StatusLiveHealthCard,
  StatusMetricsCard,
  StatusRecentWorkflowsCard,
  StatusWorkloadHealthCard,
} from "@/components/screens/clusters/status/ClusterStatusScreen";
import { useClusterHealth } from "@/components/screens/clusters/status/use-cluster-health";
import { useClusterLifecycleAudit } from "@/components/screens/clusters/status/use-cluster-lifecycle-audit";
import { useClusterLiveState } from "@/components/screens/clusters/status/use-cluster-live-state";
import { useClusterMetrics } from "@/components/screens/clusters/status/use-cluster-metrics";
import {
  useClusterWorkloadHealth,
  useClusterWorkloadList,
} from "@/components/screens/clusters/status/use-cluster-workload-health";
import { useRecentClusterWorkflows } from "@/components/screens/clusters/status/use-recent-cluster-workflows";

/*
 * Containers for the cluster Status, Health and Activity tabs. Each one
 * mounts its hook only when it renders, so a card the Status body leaves
 * out while the cluster is offline never fires its driver query.
 */

type ClusterProps = { clusterId: string };

/** Status tab: the live state decides which driver-dependent cards mount. */
export function ClusterStatusContainer({ clusterId, slug }: ClusterProps & { slug: string }) {
  return (
    <ClusterStatusBody
      slug={slug}
      liveState={useClusterLiveState(clusterId)}
      metrics={<StatusMetricsContainer clusterId={clusterId} slug={slug} />}
      workloads={<StatusWorkloadHealthContainer clusterId={clusterId} slug={slug} />}
      liveHealth={<StatusLiveHealthContainer clusterId={clusterId} />}
      workflows={<StatusRecentWorkflowsContainer clusterId={clusterId} slug={slug} />}
      lifecycle={<StatusLifecycleContainer clusterId={clusterId} slug={slug} />}
    />
  );
}

function StatusMetricsContainer({ clusterId, slug }: ClusterProps & { slug: string }) {
  return <StatusMetricsCard slug={slug} {...useClusterMetrics(clusterId)} />;
}

function StatusWorkloadHealthContainer({ clusterId, slug }: ClusterProps & { slug: string }) {
  return <StatusWorkloadHealthCard slug={slug} {...useClusterWorkloadHealth(clusterId)} />;
}

// The overview shows the top five of each; the Health and Activity tabs
// hold the whole list or feed and fetch it there.
function StatusLiveHealthContainer({ clusterId }: ClusterProps) {
  return <StatusLiveHealthCard {...useClusterHealth(clusterId, 5)} />;
}

function StatusRecentWorkflowsContainer({ clusterId, slug }: ClusterProps & { slug: string }) {
  return (
    <StatusRecentWorkflowsCard
      slug={slug}
      {...useRecentClusterWorkflows(clusterId, { limit: 5 })}
    />
  );
}

function StatusLifecycleContainer({ clusterId, slug }: ClusterProps & { slug: string }) {
  return <StatusLifecycleCard slug={slug} {...useClusterLifecycleAudit(clusterId, { limit: 5 })} />;
}

/** Health tab body. */
export function ClusterHealthContainer({ clusterId }: ClusterProps) {
  return (
    <ClusterHealthBody
      health={useClusterHealth(clusterId, 25)}
      workloads={useClusterWorkloadList(clusterId)}
    />
  );
}

/** Activity tab body. */
export function ClusterActivityContainer({ clusterId }: ClusterProps) {
  return (
    <ClusterActivityBody
      workflows={useRecentClusterWorkflows(clusterId, { limit: 25, pollInterval: 15000 })}
      lifecycle={useClusterLifecycleAudit(clusterId, { limit: 50, pollInterval: 30000 })}
    />
  );
}
