"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  ServerIcon,
  ServerOffIcon,
  WifiOffIcon,
} from "lucide-react";
import Link from "next/link";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { AdministrationShell } from "@/components/screens/administration/organization/AdministrationShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import {
  clusterOfflineMessage,
  heartbeatPresentation,
  isClusterLive,
} from "@/lib/cluster-heartbeat";

import {
  TONE_COLORS,
  WINDOWS,
  type MetricsCluster,
  type MetricsWindow,
  type Tone,
  type WindowLabel,
} from "./metrics-format";
import type { useAdminMetrics } from "./use-admin-metrics";

export type AdminMetricsScreenProps = ReturnType<typeof useAdminMetrics> & {
  /**
   * The Prometheus + system-metrics panels for one live cluster. A render
   * prop so each cluster's metric queries run only when it is mounted:
   * the route passes containers that call the panel hooks, a story passes
   * the panel views with fixtures.
   */
  renderLivePanels: (cluster: MetricsCluster, win: MetricsWindow) => React.ReactNode;
};

/** Platform metrics across the organization's clusters. */
export function AdminMetricsScreen({
  windowLabel,
  onWindowChange,
  win,
  clusters,
  loading,
  error,
  onRetry,
  renderLivePanels,
}: AdminMetricsScreenProps) {
  return (
    <AdministrationShell
      fnKey="metrics"
      title="Platform metrics"
      description="Fleet health, in-cluster Prometheus saturation, and cloud-provider system metrics across your organization's clusters."
      primaryAction={<WindowSelector value={windowLabel} onChange={onWindowChange} />}
    >
      {error && clusters.length === 0 ? (
        <EmptyState
          icon={<AlertTriangleIcon className="text-danger size-5" />}
          title="Could not load clusters"
          description={error.message}
          secondary={
            <Button size="sm" variant="outline" onClick={onRetry}>
              Retry
            </Button>
          }
        />
      ) : loading && clusters.length === 0 ? (
        <div className="space-y-4">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : clusters.length === 0 ? (
        <EmptyState
          icon={<ServerIcon className="size-5" />}
          title="No clusters registered"
          description="Register a tenant cluster to start collecting platform metrics. Cluster saturation and system metrics populate once a cluster is managed and reachable."
          actionHref="/clusters"
          actionLabel="Open clusters"
        />
      ) : (
        <div className="flex min-w-0 flex-col gap-8">
          <Section title="Fleet">
            <FleetSummary clusters={clusters} />
          </Section>
          <Section title="Clusters">
            {clusters.map((c) => (
              <ClusterSection
                key={c.id}
                cluster={c}
                win={win}
                renderLivePanels={renderLivePanels}
              />
            ))}
          </Section>
        </div>
      )}
    </AdministrationShell>
  );
}

// ─── Fleet summary strip ──────────────────────────────────────────────
function FleetSummary({ clusters }: { clusters: MetricsCluster[] }) {
  const total = clusters.length;
  const connected = clusters.filter((c) => c.heartbeatStatus === "connected").length;
  const degraded = clusters.filter((c) => c.heartbeatStatus === "degraded").length;
  const offline = clusters.filter(
    (c) => c.heartbeatStatus === "offline" || c.heartbeatStatus === "never_seen"
  ).length;

  const tiles: { label: string; value: number; tone: Tone }[] = [
    { label: "Clusters", value: total, tone: "neutral" },
    { label: "Connected", value: connected, tone: connected > 0 ? "ok" : "neutral" },
    { label: "Degraded", value: degraded, tone: degraded > 0 ? "warn" : "neutral" },
    { label: "Offline", value: offline, tone: offline > 0 ? "bad" : "neutral" },
  ];

  return (
    <Card>
      <CardContent className="grid grid-cols-2 gap-4 p-4 sm:grid-cols-4">
        {tiles.map((t) => (
          <div key={t.label} className="min-w-0 space-y-1">
            <p className="text-muted-foreground text-2xs font-medium tracking-wider uppercase">
              {t.label}
            </p>
            <p
              className={`font-mono text-2xl font-semibold tabular-nums ${TONE_COLORS[t.tone].text}`}
            >
              {t.value}
            </p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

// ─── Per-cluster section ──────────────────────────────────────────────
// Gates the driver/network-backed panels on the cheap heartbeat status
// already carried by LIST_CLUSTERS: when a cluster is offline / has no
// agent, we render a targeted placeholder INSTEAD of mounting the metric
// panels, so their Prometheus / CloudWatch queries never fire against an
// unreachable control-plane target (the "tabs spin forever" problem the
// keep-alive heartbeat was built to short-circuit).
function ClusterSection({
  cluster,
  win,
  renderLivePanels,
}: {
  cluster: MetricsCluster;
  win: MetricsWindow;
  renderLivePanels: AdminMetricsScreenProps["renderLivePanels"];
}) {
  const live = isClusterLive(cluster.heartbeatStatus);
  const p = heartbeatPresentation(cluster.heartbeatStatus);

  return (
    <Card className="min-w-0">
      <CardHeader className="px-4 pt-4 pb-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <ServerIcon className="text-muted-foreground size-4 shrink-0" />
          <CardTitle className="min-w-0 text-sm font-semibold [overflow-wrap:anywhere]">
            {cluster.name}
          </CardTitle>
          <span className="text-muted-foreground min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
            {cluster.slug}
          </span>
          <Badge variant="secondary" className="max-w-full font-mono whitespace-normal">
            {cluster.providerPluginSlug}
          </Badge>
          {cluster.region && (
            <span className="text-muted-foreground font-mono text-xs">{cluster.region}</span>
          )}
          <span
            className={`ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${p.pill}`}
          >
            {live ? (
              <ActivityIcon className="size-3" />
            ) : cluster.heartbeatStatus === "never_seen" ? (
              <ServerOffIcon className="size-3" />
            ) : (
              <WifiOffIcon className="size-3" />
            )}
            {p.label}
          </span>
        </div>
      </CardHeader>
      <CardContent className="min-w-0 space-y-6 px-4 pb-4">
        {live ? (
          renderLivePanels(cluster, win)
        ) : (
          <div className="border-border/70 text-muted-foreground flex items-start gap-3 rounded-md border border-dashed px-3 py-4 text-sm">
            <WifiOffIcon className="text-muted-foreground/70 mt-0.5 size-4 shrink-0" />
            <div className="min-w-0 space-y-1">
              <p className="[overflow-wrap:anywhere]">
                {clusterOfflineMessage(cluster.heartbeatStatus, cluster.heartbeatAgeSeconds)}
              </p>
              <Link
                href={`/clusters/${cluster.slug}/status`}
                className="text-primary inline-block text-xs underline-offset-4 hover:underline"
              >
                Open cluster status →
              </Link>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Window selector ──────────────────────────────────────────────────
function WindowSelector({
  value,
  onChange,
}: {
  value: WindowLabel;
  onChange: (w: WindowLabel) => void;
}) {
  return (
    <div className="bg-muted/40 inline-flex rounded-md border p-0.5">
      {WINDOWS.map((w) => (
        <Button
          key={w.label}
          variant={value === w.label ? "default" : "ghost"}
          size="sm"
          className="h-7 px-3 font-mono text-xs"
          onClick={() => onChange(w.label)}
        >
          {w.label}
        </Button>
      ))}
    </div>
  );
}
