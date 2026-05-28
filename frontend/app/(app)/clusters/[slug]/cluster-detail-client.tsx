"use client";

import { useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckCircleIcon,
  LayersIcon,
  Loader2Icon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { ClusterTabs } from "./components/cluster-tabs";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  CLUSTER_APP_COUNT,
  LIST_CLUSTERS,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

interface Resp {
  astroliftClusters: AstroliftTenantCluster[];
}

const POLL_INTERVAL_MS = 4000;

type Lifecycle = "registered" | "managing" | "managed" | "error";

const PROVIDER_LABEL: Record<string, string> = {
  k8s_native: "Kubernetes (native)",
  eks: "AWS EKS",
  gke: "Google GKE",
  aks: "Azure AKS",
  k3s: "k3s",
  kind: "kind",
};

const LIFECYCLE_PRESENTATION: Record<
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

// Probed-capability rows the operator cares about, in the order the
// management report renders them. Each entry derives its row from
// the JSON shape persisted on TenantCluster.capabilities. Missing
// keys fall back to "Not detected" rather than blowing up the page —
// older rows from before #316 will have an empty JSONField until the
// operator clicks Refresh.
const CAPABILITY_KEYS = [
  "cert_manager",
  "ingress",
  "service_mesh",
  "external_dns",
  "storage_classes",
  "metrics_server",
  "prometheus",
] as const;

export function ClusterDetailClient({ slug }: { slug: string }) {
  const { data, loading, startPolling, stopPolling } = useQuery<Resp>(LIST_CLUSTERS);
  const cluster = (data?.astroliftClusters ?? []).find((c) => c.slug === slug);
  const lifecycle = (cluster?.lifecycle as Lifecycle | undefined) ?? "registered";

  // Mirror the list page's polling — keep refetching while the
  // workflow's still in flight, stop the moment we settle.
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
  const caps = (cluster.capabilities ?? {}) as Record<string, unknown>;
  const lifecyclePresentation = LIFECYCLE_PRESENTATION[lifecycle];

  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          <span className="bg-muted text-muted-foreground rounded-md p-1.5">
            <LayersIcon className="size-4" />
          </span>
          {cluster.name}
        </span>
      }
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{cluster.slug}</span>
          <Badge variant="secondary">{provider}</Badge>
          {cluster.region && <Badge variant="outline">{cluster.region}</Badge>}
          {!cluster.isActive && <Badge variant="destructive">inactive</Badge>}
          <Badge variant={lifecyclePresentation.variant} className="gap-1">
            {lifecyclePresentation.icon}
            {lifecyclePresentation.label}
          </Badge>
        </span>
      }
    >
      <ClusterTabs slug={slug} active="overview" />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-muted-foreground text-sm">Status</CardTitle>
            <StatusDot status={cluster.isActive ? "ok" : "error"} />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold capitalize">
              {lifecyclePresentation.label.replace("…", "")}
            </p>
            <p className="text-muted-foreground mt-1 text-xs">
              {cluster.managedAt
                ? `Managed since ${new Date(cluster.managedAt).toLocaleString()}`
                : cluster.capabilitiesProbedAt
                  ? `Probed ${new Date(cluster.capabilitiesProbedAt).toLocaleString()}`
                  : "Not yet brought into management"}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">Provider</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm">{provider}</p>
            <p className="text-muted-foreground font-mono text-xs">{cluster.providerPluginSlug}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">Region</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="font-mono text-sm">{cluster.region || "—"}</p>
          </CardContent>
        </Card>

        <AppsBoundCard clusterId={cluster.id} />
      </div>

      {Object.keys(caps).length > 0 && (
        <div className="flex flex-wrap gap-2">
          {CAPABILITY_KEYS.map((key) => {
            const p = formatCapability(key, caps[key]);
            return (
              <Badge key={key} variant={p.installed ? "default" : "outline"} className="gap-1">
                {p.installed ? (
                  <CheckCircleIcon className="size-3" />
                ) : (
                  <AlertTriangleIcon className="size-3" />
                )}
                {p.label}
              </Badge>
            );
          })}
        </div>
      )}

      {lifecycle === "error" && cluster.lastManagementError && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2 text-base">
              <AlertTriangleIcon className="size-4" />
              Last management failure
            </CardTitle>
            <CardDescription>
              The workflow recorded this error on its most recent attempt. Fix the underlying issue
              and click Retry to re-run.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="text-destructive text-xs whitespace-pre-wrap">
              {cluster.lastManagementError}
            </pre>
          </CardContent>
        </Card>
      )}

      {lifecycle !== "managed" && lifecycle !== "managing" && (
        <div className="border-border bg-muted/40 rounded-md border p-4 text-sm">
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

function formatCapability(
  key: string,
  value: unknown
): { label: string; installed: boolean; detail: string } {
  if (key === "cert_manager") {
    const v = (value ?? {}) as {
      installed?: boolean;
      version?: string | null;
      default_issuer?: string | null;
    };
    return {
      label: "cert-manager",
      installed: !!v.installed,
      detail:
        [v.version && `version ${v.version}`, v.default_issuer && `issuer ${v.default_issuer}`]
          .filter(Boolean)
          .join(" · ") || "—",
    };
  }
  if (key === "ingress") {
    const v = (value ?? {}) as {
      installed?: boolean;
      class?: string | null;
      controller_version?: string | null;
    };
    return {
      label: "ingress controller",
      installed: !!v.installed,
      detail:
        [v.class && `class ${v.class}`, v.controller_version && `version ${v.controller_version}`]
          .filter(Boolean)
          .join(" · ") || "—",
    };
  }
  if (key === "service_mesh") {
    const v = (value ?? {}) as { installed?: boolean; kind?: string | null };
    return {
      label: "service mesh",
      installed: !!v.installed,
      detail: v.kind ?? "—",
    };
  }
  if (key === "external_dns") {
    const v = (value ?? {}) as { installed?: boolean; provider?: string | null };
    return {
      label: "external-dns",
      installed: !!v.installed,
      detail: v.provider ?? "—",
    };
  }
  if (key === "storage_classes") {
    const arr = Array.isArray(value) ? (value as string[]) : [];
    return {
      label: "storage classes",
      installed: arr.length > 0,
      detail: arr.length > 0 ? arr.join(", ") : "—",
    };
  }
  if (key === "metrics_server") {
    return {
      label: "metrics-server",
      installed: !!value,
      detail: "—",
    };
  }
  if (key === "prometheus") {
    return {
      label: "Prometheus",
      installed: !!value,
      detail: "—",
    };
  }
  return {
    label: key,
    installed: false,
    detail: typeof value === "object" ? JSON.stringify(value) : String(value),
  };
}

// ─── Apps-bound count card (#393) ───────────────────────────────────────
// Single-number summary of how many active apps target this cluster
// (via default_tenant_cluster FK OR per-env binding). Backs the
// operator's question "what's the blast radius of decommissioning?".

interface AppCountResp {
  astroliftAppCountForCluster: number;
}

function AppsBoundCard({ clusterId }: { clusterId: string }) {
  const { data, loading } = useQuery<AppCountResp>(CLUSTER_APP_COUNT, {
    variables: { clusterId },
    pollInterval: 30000,
  });
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-muted-foreground text-sm">Apps bound</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-5 w-12" />
        ) : (
          <p className="text-sm">
            <span className="text-2xl font-semibold">{data?.astroliftAppCountForCluster ?? 0}</span>{" "}
            <span className="text-muted-foreground">active</span>
          </p>
        )}
      </CardContent>
    </Card>
  );
}
