"use client";

import { useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CloudIcon,
  GlobeIcon,
  LayersIcon,
  ShieldIcon,
} from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

interface Resp {
  astroliftClusters: AstroliftTenantCluster[];
}

const PROVIDER_LABEL: Record<string, string> = {
  k8s_native: "Kubernetes (native)",
  eks: "AWS EKS",
  gke: "Google GKE",
  aks: "Azure AKS",
  k3s: "k3s",
  kind: "kind",
};

export function ClusterDetailClient({ slug }: { slug: string }) {
  const { data, loading } = useQuery<Resp>(LIST_CLUSTERS);
  const cluster = (data?.astroliftClusters ?? []).find((c) => c.slug === slug);

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
  const capEntries = Object.entries(caps);

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
          <Badge variant="outline">{cluster.region}</Badge>
          {!cluster.isActive && (
            <Badge variant="destructive">inactive</Badge>
          )}
        </span>
      }
    >
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-muted-foreground text-sm">Status</CardTitle>
            <StatusDot status={cluster.isActive ? "ok" : "error"} />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold capitalize">
              {cluster.isActive ? "Active" : "Inactive"}
            </p>
            <p className="text-muted-foreground mt-1 text-xs">
              {cluster.capabilitiesProbedAt
                ? `Probed ${new Date(cluster.capabilitiesProbedAt).toLocaleString()}`
                : "Capabilities not yet probed"}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">Provider</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm">{provider}</p>
            <p className="text-muted-foreground font-mono text-xs">
              {cluster.providerPluginSlug}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-muted-foreground text-sm">Region</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="font-mono text-sm">{cluster.region}</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <GlobeIcon className="size-4" />
            Connection
          </CardTitle>
          <CardDescription>
            How the platform reaches the cluster's API server.
          </CardDescription>
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
          <Field
            label="Registered"
            value={new Date(cluster.createdAt).toLocaleString()}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <CloudIcon className="size-4" />
            Capabilities
            {capEntries.length > 0 && (
              <Badge variant="outline" className="ml-2">
                {capEntries.length}
              </Badge>
            )}
          </CardTitle>
          <CardDescription>
            Reported by the provider plugin during the last capability probe.
            Empty until the first probe completes.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {capEntries.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No capabilities reported yet.
            </p>
          ) : (
            <dl className="grid grid-cols-1 gap-x-8 gap-y-2 sm:grid-cols-2">
              {capEntries.map(([key, value]) => (
                <div key={key}>
                  <dt className="text-muted-foreground text-xs uppercase tracking-wide">
                    {key}
                  </dt>
                  <dd className="font-mono text-xs">
                    {typeof value === "object" && value !== null
                      ? JSON.stringify(value)
                      : String(value)}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </CardContent>
      </Card>
    </PageShell>
  );
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
      <dt className="text-muted-foreground text-xs uppercase tracking-wide">
        {label}
      </dt>
      <dd className={mono ? "font-mono text-sm break-all" : "text-sm"}>{value}</dd>
    </div>
  );
}
