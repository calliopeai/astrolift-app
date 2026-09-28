"use client";

import {
  Activity,
  AlertTriangleIcon,
  CheckCircleIcon,
  GlobeIcon,
  HardDriveIcon,
  LayersIcon,
  Loader2Icon,
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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";

import { ClusterTabs } from "./ClusterTabs";
import type { useClusterDetail } from "./use-cluster-detail";

export type ClusterDetailProps = ReturnType<typeof useClusterDetail> & {
  /** The live stats row, rendered by the caller so its polling queries stay out of this view. */
  renderLiveStats: (clusterId: string) => React.ReactNode;
};

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
  | "external_dns"
  | "storage_classes"
  | "metrics_server"
  | "prometheus";

const CAPABILITY_META: Record<CapKey, { label: string; icon: React.ReactNode }> = {
  cert_manager: {
    label: "cert-manager",
    icon: <ShieldCheckIcon className="size-4" />,
  },
  ingress: {
    label: "Ingress",
    icon: <NetworkIcon className="size-4" />,
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

/**
 * The cluster overview tab. Pure view; the data half is useClusterDetail.
 */
export function ClusterDetail({ slug, cluster, loading, renderLiveStats }: ClusterDetailProps) {
  const fmt = useFormatters();
  const lifecycle = (cluster?.lifecycle as Lifecycle | undefined) ?? "registered";

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
      <Card className="!rounded-none border-l-4 border-l-neutral-600 shadow-md dark:border-l-neutral-500">
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardTitle className="text-base">Management</CardTitle>
              <CardDescription className="mt-0.5">
                {lifecycle === "managed" && cluster.managedAt
                  ? `Active since ${fmt.formatDateTime(cluster.managedAt)}`
                  : cluster.capabilitiesProbedAt
                    ? `Capabilities probed ${fmt.formatDateTime(cluster.capabilitiesProbedAt)}`
                    : "Not yet brought into management"}
              </CardDescription>
            </div>
            <Badge variant={lc.variant} className="shrink-0 gap-1">
              {lc.icon}
              {lc.label}
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">Provider</dt>
              <dd className="mt-0.5 font-medium">{provider}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">Region</dt>
              <dd className="mt-0.5 font-mono">{cluster.region || "—"}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">Auth</dt>
              <dd className="mt-0.5 font-mono">{cluster.authMethod || "—"}</dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      {/* ── Live stats row ──────────────────────────────────────────── */}
      {renderLiveStats(cluster.id)}

      {/* ── Capabilities grid ───────────────────────────────────────── */}
      {hasCaps && (
        <Card className="!rounded-none shadow-md">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Capabilities</CardTitle>
            <CardDescription>
              Detected during the last capability probe. Refresh cluster management to update.
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
                      installed ? "border-border bg-muted/40" : "border-border bg-muted/30"
                    }`}
                  >
                    <span
                      className={`shrink-0 p-1.5 ${
                        installed ? "bg-muted text-foreground" : "bg-muted text-muted-foreground"
                      }`}
                    >
                      {meta.icon}
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-sm leading-snug font-medium">{meta.label}</p>
                      <p className="text-muted-foreground mt-0.5 text-xs">
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
        <Card className="border-l-destructive border-destructive/40 bg-destructive/5 !rounded-none border-l-4 shadow-md">
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
