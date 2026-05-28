"use client";

import { useQuery } from "@apollo/client/react";
import {
  Activity,
  AlertTriangleIcon,
  CheckCircle2Icon,
  CheckCircleIcon,
  DatabaseIcon,
  GlobeIcon,
  HardDriveIcon,
  LayersIcon,
  Loader2Icon,
  LockIcon,
  NetworkIcon,
  ServerIcon,
  ShieldCheckIcon,
  TrendingUpIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

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
  CLUSTER_APP_COUNT,
  CLUSTER_PROMETHEUS_METRICS,
  LIST_CLUSTERS,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

import { ClusterTabs } from "./components/cluster-tabs";

// ─── Types ────────────────────────────────────────────────────────────
interface Resp {
  astroliftClusters: AstroliftTenantCluster[];
}

type Lifecycle = "registered" | "managing" | "managed" | "error";

// ─── Static maps ─────────────────────────────────────────────────────
const PROVIDER_LABEL: Record<string, string> = {
  k8s_native: "Kubernetes",
  eks: "AWS EKS",
  gke: "Google GKE",
  aks: "Azure AKS",
  k3s: "k3s",
  kind: "kind",
};

const LIFECYCLE_CONFIG: Record<
  Lifecycle,
  {
    label: string;
    variant: "default" | "secondary" | "outline" | "destructive";
    icon: React.ReactNode;
  }
> = {
  registered: {
    label: "Registered",
    variant: "outline",
    icon: <LayersIcon className="size-3" />,
  },
  managing: {
    label: "Managing…",
    variant: "secondary",
    icon: <Loader2Icon className="size-3 animate-spin" />,
  },
  managed: {
    label: "Managed",
    variant: "default",
    icon: <CheckCircleIcon className="size-3" />,
  },
  error: {
    label: "Error",
    variant: "destructive",
    icon: <AlertTriangleIcon className="size-3" />,
  },
};

// ─── Capability config ────────────────────────────────────────────────
type CapKey =
  | "cert_manager"
  | "ingress"
  | "service_mesh"
  | "external_dns"
  | "storage_classes"
  | "metrics_server"
  | "prometheus";

const CAPABILITY_META: Record<
  CapKey,
  { label: string; icon: React.ReactNode }
> = {
  cert_manager: {
    label: "cert-manager",
    icon: <ShieldCheckIcon className="size-4" />,
  },
  ingress: {
    label: "Ingress",
    icon: <NetworkIcon className="size-4" />,
  },
  service_mesh: {
    label: "Service mesh",
    icon: <LockIcon className="size-4" />,
  },
  external_dns: {
    label: "external-dns",
    icon: <GlobeIcon className="size-4" />,
  },
  storage_classes: {
    label: "Storage classes",
    icon: <HardDriveIcon className="size-4" />,
  },
  metrics_server: {
    label: "Metrics server",
    icon: <Activity className="size-4" />,
  },
  prometheus: {
    label: "Prometheus",
    icon: <TrendingUpIcon className="size-4" />,
  },
};

function isCapInstalled(key: CapKey, value: unknown): boolean {
  if (key === "storage_classes") return Array.isArray(value) && value.length > 0;
  if (value && typeof value === "object") {
    return !!(value as Record<string, unknown>).installed;
  }
  return !!value;
}

// ─── Main component ───────────────────────────────────────────────────
const POLL_INTERVAL_MS = 4000;

export function ClusterDetailClient({ slug }: { slug: string }) {
  const { data, loading, startPolling, stopPolling } =
    useQuery<Resp>(LIST_CLUSTERS);
  const cluster = (data?.astroliftClusters ?? []).find((c) => c.slug === slug);
  const lifecycle = (cluster?.lifecycle as Lifecycle | undefined) ?? "registered";

  React.useEffect(() => {
    if (lifecycle === "managing") {
      startPolling(POLL_INTERVAL_MS);
      return () => stopPolling();
    }
    stopPolling();
    return undefined;
  }, [lifecycle, startPolling, stopPolling]);

  if (loading && !cluster) {
    return (
      <PageShell title="Cluster" description="Loading…">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-48 w-full" />
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

  const provider = PROVIDER_LABEL[cluster.providerPluginSlug] ?? cluster.providerPluginSlug;
  const lc = LIFECYCLE_CONFIG[lifecycle];
  const caps = (cluster.capabilities ?? {}) as Record<string, unknown>;
  const hasCaps = Object.keys(caps).length > 0;

  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          <span className="bg-muted text-muted-foreground rounded-md p-1.5">
            <ServerIcon className="size-4" />
          </span>
          {cluster.name}
        </span>
      }
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm">{cluster.slug}</span>
          <Badge variant="secondary">{provider}</Badge>
          {cluster.region && <Badge variant="outline">{cluster.region}</Badge>}
          {!cluster.isActive && <Badge variant="destructive">inactive</Badge>}
          <Badge variant={lc.variant} className="gap-1">
            {lc.icon}
            {lc.label}
          </Badge>
        </span>
      }
    >
      <ClusterTabs slug={slug} active="overview" />

      {/* ── Management status card ──────────────────────────────────── */}
      <Card className="!rounded-none shadow-md border-l-4 border-l-neutral-600 dark:border-l-neutral-500">
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardTitle className="text-base">Management</CardTitle>
              <CardDescription className="mt-0.5">
                {lifecycle === "managed" && cluster.managedAt
                  ? `Active since ${new Date(cluster.managedAt).toLocaleString()}`
                  : cluster.capabilitiesProbedAt
                    ? `Capabilities probed ${new Date(cluster.capabilitiesProbedAt).toLocaleString()}`
                    : "Not yet brought into management"}
              </CardDescription>
            </div>
            <Badge variant={lc.variant} className="gap-1 shrink-0">
              {lc.icon}
              {lc.label}
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3 text-sm">
            <div>
              <dt className="text-muted-foreground text-xs uppercase tracking-wide">Provider</dt>
              <dd className="font-medium mt-0.5">{provider}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs uppercase tracking-wide">Region</dt>
              <dd className="font-mono mt-0.5">{cluster.region || "—"}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs uppercase tracking-wide">Auth</dt>
              <dd className="font-mono mt-0.5">{cluster.authMethod || "—"}</dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      {/* ── Live stats row ──────────────────────────────────────────── */}
      <LiveStatsRow clusterId={cluster.id} />

      {/* ── Capabilities grid ───────────────────────────────────────── */}
      {hasCaps && (
        <Card className="!rounded-none shadow-md">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Capabilities</CardTitle>
            <CardDescription>
              Detected during the last capability probe. Refresh cluster
              management to update.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {(Object.keys(CAPABILITY_META) as CapKey[]).map((key) => {
                const meta = CAPABILITY_META[key];
                const installed = isCapInstalled(key, caps[key]);
                return (
                  <div
                    key={key}
                    className={`flex items-center gap-3 border p-3 ${
                      installed
                        ? "border-neutral-300 bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800/40"
                        : "border-border bg-muted/30"
                    }`}
                  >
                    <span
                      className={`p-1.5 shrink-0 ${
                        installed
                          ? "bg-neutral-200 text-neutral-700 dark:bg-neutral-700 dark:text-neutral-300"
                          : "bg-muted text-muted-foreground"
                      }`}
                    >
                      {meta.icon}
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm font-medium leading-snug truncate">{meta.label}</p>
                      <p className="text-xs mt-0.5 text-muted-foreground">
                        {installed ? "Installed" : "Not detected"}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* ── Error card ──────────────────────────────────────────────── */}
      {lifecycle === "error" && cluster.lastManagementError && (
        <Card className="!rounded-none shadow-md border-l-4 border-l-destructive border-destructive/40 bg-destructive/5">
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2 text-base">
              <AlertTriangleIcon className="size-4" />
              Last management failure
            </CardTitle>
            <CardDescription>
              Fix the underlying issue and click Retry from Settings to re-run.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="text-destructive text-xs whitespace-pre-wrap">
              {cluster.lastManagementError}
            </pre>
          </CardContent>
        </Card>
      )}

      {/* ── Action required ─────────────────────────────────────────── */}
      {lifecycle !== "managed" && lifecycle !== "managing" && (
        <div className="border-border bg-muted/40 border p-4 text-sm">
          <p className="font-medium">Action required</p>
          <p className="text-muted-foreground mt-1">
            This cluster isn&apos;t managed yet.{" "}
            <Link
              href={`/clusters/${slug}/settings`}
              className="text-primary underline-offset-4 hover:underline"
            >
              Go to Settings
            </Link>{" "}
            to bring it into management.
          </p>
        </div>
      )}
    </PageShell>
  );
}

// ─── Live stats row ───────────────────────────────────────────────────
// Apps bound (from cluster query) + Prometheus instant metrics.
// Prometheus tiles degrade gracefully to "—" when unavailable.

interface AppCountResp {
  astroliftAppCountForCluster: number;
}

interface PrometheusInstantResp {
  astroliftClusterPrometheusMetrics: {
    available: boolean;
    nodeCount: number | null;
    podRunningRatio: number | null;
    cpuUtilization: number | null;
    memoryUtilization: number | null;
    deploymentReadyRatio: number | null;
  };
}

function pct(v: number | null): string {
  if (v === null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function utilizationTone(v: number | null): string {
  if (v === null) return "text-foreground";
  if (v < 0.7) return "text-emerald-600";
  if (v < 0.9) return "text-amber-600";
  return "text-destructive";
}

function healthTone(v: number | null): string {
  if (v === null) return "text-foreground";
  if (v >= 0.9) return "text-emerald-600";
  if (v >= 0.7) return "text-amber-600";
  return "text-destructive";
}

interface StatTileProps {
  label: string;
  value: React.ReactNode;
  icon: React.ReactNode;
  valueClass?: string;
  loading?: boolean;
}

function StatTile({ label, value, icon, valueClass = "text-foreground", loading }: StatTileProps) {
  return (
    <Card className="!rounded-none shadow-md">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2 pt-4 px-4">
        <span className="text-muted-foreground text-xs uppercase tracking-wide">{label}</span>
        <span className="text-muted-foreground">{icon}</span>
      </CardHeader>
      <CardContent className="px-4 pb-4">
        {loading ? (
          <Skeleton className="h-7 w-16" />
        ) : (
          <p className={`text-2xl font-semibold tabular-nums ${valueClass}`}>{value}</p>
        )}
      </CardContent>
    </Card>
  );
}

function LiveStatsRow({ clusterId }: { clusterId: string }) {
  const { data: appData, loading: appLoading } = useQuery<AppCountResp>(
    CLUSTER_APP_COUNT,
    { variables: { clusterId }, pollInterval: 30000 },
  );
  const { data: promData, loading: promLoading } =
    useQuery<PrometheusInstantResp>(CLUSTER_PROMETHEUS_METRICS, {
      variables: { clusterId },
      pollInterval: 60000,
    });

  const m = promData?.astroliftClusterPrometheusMetrics;
  const hasLiveData = m?.available;

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      <StatTile
        label="Apps bound"
        value={appData?.astroliftAppCountForCluster ?? "—"}
        icon={<LayersIcon className="size-4" />}
        loading={appLoading}
      />
      <StatTile
        label="Nodes"
        value={hasLiveData && m.nodeCount !== null ? m.nodeCount : "—"}
        icon={<ServerIcon className="size-4" />}
        loading={promLoading && !promData}
      />
      <StatTile
        label="Pods running"
        value={hasLiveData ? pct(m.podRunningRatio) : "—"}
        icon={<CheckCircle2Icon className="size-4" />}
        valueClass={hasLiveData ? healthTone(m.podRunningRatio) : "text-muted-foreground"}
        loading={promLoading && !promData}
      />
      <StatTile
        label="CPU"
        value={hasLiveData ? pct(m.cpuUtilization) : "—"}
        icon={<Activity className="size-4" />}
        valueClass={hasLiveData ? utilizationTone(m.cpuUtilization) : "text-muted-foreground"}
        loading={promLoading && !promData}
      />
      <StatTile
        label="Memory"
        value={hasLiveData ? pct(m.memoryUtilization) : "—"}
        icon={<DatabaseIcon className="size-4" />}
        valueClass={hasLiveData ? utilizationTone(m.memoryUtilization) : "text-muted-foreground"}
        loading={promLoading && !promData}
      />
    </div>
  );
}
