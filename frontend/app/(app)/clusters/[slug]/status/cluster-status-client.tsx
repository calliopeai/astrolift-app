"use client";

import { useQuery } from "@apollo/client/react";
import { AlertTriangleIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  CLUSTER_PROMETHEUS_METRICS,
  LIST_CLUSTERS,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

import { ClusterTabs } from "../components/cluster-tabs";

interface Resp {
  astroliftClusters: AstroliftTenantCluster[];
}

/**
 * Cluster status tab — narrowly scoped to Prometheus-sourced key
 * metrics. Health (pod phases, warning events, workload readiness)
 * lives under ``/clusters/[slug]/health``; recent workflows + the
 * lifecycle audit timeline live under ``/clusters/[slug]/activity``.
 */
export function ClusterStatusClient({ slug }: { slug: string }) {
  const { data, loading } = useQuery<Resp>(LIST_CLUSTERS);
  const cluster = (data?.astroliftClusters ?? []).find((c) => c.slug === slug);

  if (loading && !cluster) {
    return (
      <PageShell title="Cluster status" description="Loading…">
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!cluster) {
    return (
      <PageShell
        title="Cluster not found"
        description="The cluster doesn't exist or you don't have permission to view it."
      >
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No cluster with slug ${slug}`}
          actionHref="/clusters"
          actionLabel="Back to clusters"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={`${cluster.name} · Status`}
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{cluster.slug}</span>
          <Badge variant="secondary">{cluster.providerPluginSlug}</Badge>
        </span>
      }
    >
      <ClusterTabs slug={slug} active="status" />

      <ClusterMetricsCard clusterId={cluster.id} />
    </PageShell>
  );
}

// ─── Cluster metrics card (#771) ─────────────────────────────────────
// Prometheus instant queries: node count, pod running ratio, CPU/memory
// utilization, deployment ready ratio. Gracefully degrades when no
// prometheus_endpoint is configured in provider_config.

interface PrometheusMetricsResp {
  astroliftClusterPrometheusMetrics: {
    available: boolean;
    reason: string | null;
    nodeCount: number | null;
    podRunningRatio: number | null;
    cpuUtilization: number | null;
    memoryUtilization: number | null;
    deploymentReadyRatio: number | null;
  };
}

function pct(ratio: number | null): string {
  if (ratio === null) return "—";
  return `${(ratio * 100).toFixed(1)}%`;
}

function MetricTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "bad";
}) {
  const colour =
    tone === "ok"
      ? "text-emerald-600"
      : tone === "warn"
        ? "text-amber-600"
        : tone === "bad"
          ? "text-destructive"
          : "text-foreground";
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-muted-foreground text-xs uppercase tracking-wide">
        {label}
      </span>
      <span className={`text-xl font-semibold tabular-nums ${colour}`}>
        {value}
      </span>
    </div>
  );
}

function utilizationTone(ratio: number | null): "ok" | "warn" | "bad" {
  if (ratio === null) return "ok";
  if (ratio < 0.7) return "ok";
  if (ratio < 0.9) return "warn";
  return "bad";
}

function ClusterMetricsCard({ clusterId }: { clusterId: string }) {
  const { data, loading } = useQuery<PrometheusMetricsResp>(
    CLUSTER_PROMETHEUS_METRICS,
    { variables: { clusterId }, pollInterval: 60000 },
  );
  const m = data?.astroliftClusterPrometheusMetrics;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Metrics</CardTitle>
        <CardDescription>
          Prometheus-sourced cluster saturation. Instant queries — node
          count, pod health, CPU / memory utilization, deployment
          readiness. Updates every 60s.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading && !m ? (
          <Skeleton className="h-16 w-full" />
        ) : !m?.available ? (
          <p className="text-muted-foreground text-sm">
            {m?.reason === "no_endpoint"
              ? "No Prometheus endpoint configured. Set prometheus_endpoint in provider_config to unlock this card."
              : "Prometheus is unreachable. Check that the endpoint in provider_config is correct and accessible from the control plane."}
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-6 sm:grid-cols-3 lg:grid-cols-5">
            <MetricTile
              label="Nodes"
              value={m.nodeCount !== null ? String(m.nodeCount) : "—"}
            />
            <MetricTile
              label="Pods running"
              value={pct(m.podRunningRatio)}
              tone={
                m.podRunningRatio !== null && m.podRunningRatio >= 0.9
                  ? "ok"
                  : "warn"
              }
            />
            <MetricTile
              label="CPU utilization"
              value={pct(m.cpuUtilization)}
              tone={utilizationTone(m.cpuUtilization)}
            />
            <MetricTile
              label="Memory utilization"
              value={pct(m.memoryUtilization)}
              tone={utilizationTone(m.memoryUtilization)}
            />
            <MetricTile
              label="Deployments ready"
              value={pct(m.deploymentReadyRatio)}
              tone={
                m.deploymentReadyRatio !== null &&
                m.deploymentReadyRatio >= 0.9
                  ? "ok"
                  : "warn"
              }
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
