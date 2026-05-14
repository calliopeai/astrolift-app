"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckCircleIcon,
  CloudIcon,
  GlobeIcon,
  LayersIcon,
  Loader2Icon,
  PlayIcon,
  RefreshCcwIcon,
  ShieldIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  BRING_CLUSTER_INTO_MANAGEMENT,
  LIST_CLUSTERS,
  REFRESH_CLUSTER_MANAGEMENT,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

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
  { label: string; variant: "default" | "secondary" | "outline" | "destructive"; icon: React.ReactNode }
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

  const [bring, { loading: bringing }] = useMutation<{
    bringClusterIntoManagement: MutationResult<AstroliftTenantCluster>;
  }>(BRING_CLUSTER_INTO_MANAGEMENT, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });
  const [refresh, { loading: refreshing }] = useMutation<{
    refreshClusterManagement: MutationResult<AstroliftTenantCluster>;
  }>(REFRESH_CLUSTER_MANAGEMENT, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });

  async function handleBring() {
    if (!cluster) return;
    const { data } = await bring({ variables: { input: { clusterId: cluster.id } } });
    if (data?.bringClusterIntoManagement.ok) {
      toast.success(`Bringing ${cluster.slug} into management`);
    } else {
      toast.error(data?.bringClusterIntoManagement.errors?.[0]?.message ?? "Failed");
    }
  }

  async function handleRefresh(forcePreflight: boolean) {
    if (!cluster) return;
    const { data } = await refresh({
      variables: { input: { clusterId: cluster.id, forcePreflight } },
    });
    if (data?.refreshClusterManagement.ok) {
      toast.success(
        forcePreflight ? `Refreshing ${cluster.slug} (full preflight)` : `Refreshing ${cluster.slug}`
      );
    } else {
      toast.error(data?.refreshClusterManagement.errors?.[0]?.message ?? "Failed");
    }
  }

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

  // The header action button is lifecycle-aware — same logic as the
  // list view, just bigger.
  const actionButton: React.ReactNode = (() => {
    if (lifecycle === "managing") {
      return (
        <Button variant="ghost" disabled>
          <Loader2Icon className="size-4 animate-spin" />
          Setup in progress…
        </Button>
      );
    }
    if (lifecycle === "managed") {
      return (
        <Can permission="cluster.manage">
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => handleRefresh(false)} disabled={refreshing}>
              <RefreshCcwIcon className="size-4" />
              Refresh setup
            </Button>
            <Button variant="ghost" onClick={() => handleRefresh(true)} disabled={refreshing}>
              <PlayIcon className="size-4" />
              Re-run preflight
            </Button>
          </div>
        </Can>
      );
    }
    if (lifecycle === "error") {
      return (
        <Can permission="cluster.manage">
          <Button onClick={handleBring} disabled={bringing}>
            <PlayIcon className="size-4" />
            Retry bring into management
          </Button>
        </Can>
      );
    }
    return (
      <Can permission="cluster.manage">
        <Button onClick={handleBring} disabled={bringing}>
          <PlayIcon className="size-4" />
          Bring into management
        </Button>
      </Can>
    );
  })();

  // A managed cluster with one of the headline prereqs missing earns
  // the prereq-docs cross-link below the capabilities table.
  const certManager = (caps.cert_manager ?? {}) as { installed?: boolean };
  const ingress = (caps.ingress ?? {}) as { installed?: boolean };
  const missingHeadlinePrereq =
    lifecycle === "managed" && (!certManager.installed || !ingress.installed);

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
      actions={actionButton}
    >
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
      </div>

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
            <pre className="text-destructive whitespace-pre-wrap text-xs">
              {cluster.lastManagementError}
            </pre>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <GlobeIcon className="size-4" />
            Connection
          </CardTitle>
          <CardDescription>How the platform reaches the cluster&apos;s API server.</CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
          <Field label="API endpoint" mono value={cluster.endpoint} />
          <Field
            label="Auth method"
            value={
              <span className="inline-flex items-center gap-1.5">
                <ShieldIcon className="size-3.5" />
                <span className="font-mono">{cluster.authMethod}</span>
              </span>
            }
          />
          <Field label="Ingress class" mono value={cluster.ingressClass} />
          <Field label="Registered" value={new Date(cluster.createdAt).toLocaleString()} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <CloudIcon className="size-4" />
            Probed capabilities
          </CardTitle>
          <CardDescription>
            Reported by the management workflow on the most recent run. Empty until the cluster is
            brought into management; click Refresh setup to re-probe.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {Object.keys(caps).length === 0 ? (
            <p className="text-muted-foreground p-6 text-sm">
              No capabilities reported yet. Click <strong>Bring into management</strong> to run the
              probe.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Capability</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Detail</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {CAPABILITY_KEYS.map((key) => (
                  <CapabilityRow key={key} keyName={key} value={caps[key]} />
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {missingHeadlinePrereq && (
        <div className="border-muted-foreground/30 bg-muted/30 rounded-md border border-dashed p-4 text-sm">
          <p className="font-medium">Missing platform prerequisites</p>
          <p className="text-muted-foreground mt-1">
            One or more headline prereqs (cert-manager, ingress controller) aren&apos;t detected.
            Tenant deploys may fail at TLS / ingress provisioning. See{" "}
            <Link
              href="/documentation/cluster-prerequisites"
              className="text-primary underline-offset-4 hover:underline"
            >
              cluster prerequisites
            </Link>
            .
          </p>
        </div>
      )}
    </PageShell>
  );
}

// Per-row capability renderer. Each key in the JSONField shape has
// a small adapter that pulls out the user-facing status string +
// detail line — keeping the table readable without forcing the
// operator to decode the JSON.
function CapabilityRow({ keyName, value }: { keyName: string; value: unknown }) {
  const presentation = formatCapability(keyName, value);
  return (
    <TableRow>
      <TableCell className="font-mono text-xs">{presentation.label}</TableCell>
      <TableCell>
        {presentation.installed ? (
          <Badge variant="default" className="gap-1">
            <CheckCircleIcon className="size-3" />
            Installed
          </Badge>
        ) : (
          <Badge variant="outline" className="gap-1">
            Not detected
          </Badge>
        )}
      </TableCell>
      <TableCell className="text-muted-foreground text-xs">{presentation.detail}</TableCell>
    </TableRow>
  );
}

function formatCapability(
  key: string,
  value: unknown
): { label: string; installed: boolean; detail: string } {
  if (key === "cert_manager") {
    const v = (value ?? {}) as { installed?: boolean; version?: string | null; default_issuer?: string | null };
    return {
      label: "cert-manager",
      installed: !!v.installed,
      detail: [v.version && `version ${v.version}`, v.default_issuer && `issuer ${v.default_issuer}`]
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
      detail: [v.class && `class ${v.class}`, v.controller_version && `version ${v.controller_version}`]
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
  return { label: key, installed: false, detail: typeof value === "object" ? JSON.stringify(value) : String(value) };
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs uppercase tracking-wide">{label}</dt>
      <dd className={mono ? "font-mono text-sm break-all" : "text-sm"}>{value}</dd>
    </div>
  );
}
