"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckCircleIcon,
  LayersIcon,
  Loader2Icon,
  PlayIcon,
  PlusIcon,
  RefreshCcwIcon,
  Trash2Icon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  BRING_CLUSTER_INTO_MANAGEMENT,
  LIST_CLUSTERS,
  REFRESH_CLUSTER_MANAGEMENT,
  UNREGISTER_TENANT_CLUSTER,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

import { RegisterClusterDialog } from "./register-cluster-dialog";

interface Resp {
  astroliftClusters: AstroliftTenantCluster[];
}

// Active polling cadence while any row is in the "managing" state.
// 4 seconds keeps the UI responsive to the workflow (which typically
// completes in 10-30s) without hammering the apiserver. Stops polling
// as soon as no row is managing.
const POLL_INTERVAL_MS = 4000;

type Lifecycle = "registered" | "managing" | "managed" | "error";

function LifecycleBadge({ lifecycle, error }: { lifecycle: Lifecycle; error?: string }) {
  // The lifecycle badge is the single most informative cell in the row
  // — semantic color and icon convey state without forcing the operator
  // to read the slug. Tooltip carries the error message on the error
  // state so the operator can fix without leaving the list.
  const presentation: Record<
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
  const p = presentation[lifecycle];
  const badge = (
    <Badge variant={p.variant} className="gap-1">
      {p.icon}
      {p.label}
    </Badge>
  );
  if (lifecycle === "error" && error) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span>{badge}</span>
        </TooltipTrigger>
        <TooltipContent className="max-w-sm whitespace-pre-wrap text-xs">
          {error}
        </TooltipContent>
      </Tooltip>
    );
  }
  return badge;
}

export function ClustersClient() {
  const [open, setOpen] = React.useState(false);
  const [unregisterTarget, setUnregisterTarget] = React.useState<AstroliftTenantCluster | null>(
    null
  );

  const { data, loading, startPolling, stopPolling } = useQuery<Resp>(LIST_CLUSTERS, {
    notifyOnNetworkStatusChange: true,
  });

  // Poll while any row is in flight. Stop the moment all rows are in a
  // terminal state — Apollo will hold the cache for follow-up renders.
  const list = data?.astroliftClusters ?? [];
  const anyManaging = list.some((c) => c.lifecycle === "managing");
  React.useEffect(() => {
    if (anyManaging) {
      startPolling(POLL_INTERVAL_MS);
      return () => stopPolling();
    }
    stopPolling();
    return undefined;
  }, [anyManaging, startPolling, stopPolling]);

  const [unregister, { loading: deleting }] = useMutation<{
    unregisterTenantCluster: MutationResult<{ id: string; deleted: boolean }>;
  }>(UNREGISTER_TENANT_CLUSTER, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });
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

  async function handleUnregister(c: AstroliftTenantCluster) {
    const { data } = await unregister({ variables: { input: { id: c.id } } });
    if (data?.unregisterTenantCluster.ok) {
      toast.success(`Unregistered ${c.slug}`);
    } else {
      throw new Error(data?.unregisterTenantCluster.errors?.[0]?.message ?? "Failed");
    }
  }

  async function handleBring(c: AstroliftTenantCluster) {
    const { data } = await bring({ variables: { input: { clusterId: c.id } } });
    if (data?.bringClusterIntoManagement.ok) {
      toast.success(`Bringing ${c.slug} into management — this can take up to a minute.`);
    } else {
      toast.error(data?.bringClusterIntoManagement.errors?.[0]?.message ?? "Failed");
    }
  }

  async function handleRefresh(c: AstroliftTenantCluster, forcePreflight = false) {
    const { data } = await refresh({
      variables: { input: { clusterId: c.id, forcePreflight } },
    });
    if (data?.refreshClusterManagement.ok) {
      toast.success(
        forcePreflight
          ? `Refreshing ${c.slug} (full preflight)`
          : `Refreshing ${c.slug}`
      );
    } else {
      toast.error(data?.refreshClusterManagement.errors?.[0]?.message ?? "Failed");
    }
  }

  function renderActionButton(c: AstroliftTenantCluster) {
    const lifecycle = c.lifecycle as Lifecycle;
    if (lifecycle === "managing") {
      return (
        <Button size="sm" variant="ghost" disabled>
          <Loader2Icon className="size-4 animate-spin" />
          Setup in progress…
        </Button>
      );
    }
    if (lifecycle === "managed") {
      return (
        <Can permission="cluster.manage">
          <Button
            size="sm"
            variant="outline"
            onClick={() => handleRefresh(c, false)}
            disabled={refreshing}
          >
            <RefreshCcwIcon className="size-4" />
            Refresh setup
          </Button>
        </Can>
      );
    }
    if (lifecycle === "error") {
      return (
        <Can permission="cluster.manage">
          <Button
            size="sm"
            variant="outline"
            onClick={() => handleBring(c)}
            disabled={bringing}
          >
            <PlayIcon className="size-4" />
            Retry
          </Button>
        </Can>
      );
    }
    // registered (default)
    return (
      <Can permission="cluster.manage">
        <Button size="sm" onClick={() => handleBring(c)} disabled={bringing}>
          <PlayIcon className="size-4" />
          Bring into management
        </Button>
      </Can>
    );
  }

  return (
    <PageShell
      title="Clusters"
      description="Tenant Kubernetes clusters registered with the platform. Register a cluster to record its metadata, then click Bring into management when its prereqs (cert-manager, ingress controller) are installed."
      actions={
        <Can permission="cluster.register">
          <Button onClick={() => setOpen(true)}>
            <PlusIcon className="size-4" />
            Register cluster
          </Button>
        </Can>
      }
    >
      <Card>
        <CardContent className="p-0">
          {loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<LayersIcon className="size-5" />}
                title="No clusters registered"
                description="Register a cluster manually here or run the install playbook for your cloud (astrolift-opscode/INSTALL-<cloud>.md)."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead></TableHead>
                  <TableHead>Cluster</TableHead>
                  <TableHead>Lifecycle</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>Region</TableHead>
                  <TableHead>Ingress</TableHead>
                  <TableHead>Last probe</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="w-8">
                      <StatusDot status={c.isActive ? "ok" : "muted"} />
                    </TableCell>
                    <TableCell>
                      <a href={`/clusters/${c.slug}`} className="hover:underline">
                        <div className="font-medium">{c.name}</div>
                        <div className="text-muted-foreground font-mono text-xs">{c.slug}</div>
                      </a>
                    </TableCell>
                    <TableCell>
                      <LifecycleBadge
                        lifecycle={(c.lifecycle as Lifecycle) ?? "registered"}
                        error={c.lastManagementError ?? undefined}
                      />
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{c.providerPluginSlug}</Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{c.region || "—"}</TableCell>
                    <TableCell className="font-mono text-xs">{c.ingressClass}</TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {c.capabilitiesProbedAt
                        ? new Date(c.capabilitiesProbedAt).toLocaleString()
                        : "never"}
                    </TableCell>
                    <TableCell className="flex items-center justify-end gap-2 text-right">
                      {renderActionButton(c)}
                      <Can permission="cluster.unregister">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setUnregisterTarget(c)}
                          disabled={deleting}
                        >
                          <Trash2Icon className="size-4" />
                          <span className="sr-only">Unregister</span>
                        </Button>
                      </Can>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <RegisterClusterDialog open={open} onOpenChange={setOpen} />

      <ConfirmDialog
        open={unregisterTarget !== null}
        onOpenChange={(next) => {
          if (!next) setUnregisterTarget(null);
        }}
        title={
          unregisterTarget ? `Unregister cluster ${unregisterTarget.slug}?` : "Unregister cluster?"
        }
        description="Refused if any active app still targets this cluster. The cluster's kubeconfig and probed capabilities are removed from the control plane."
        confirmLabel="Unregister"
        destructive
        onConfirm={async () => {
          if (unregisterTarget) await handleUnregister(unregisterTarget);
        }}
      />
    </PageShell>
  );
}
