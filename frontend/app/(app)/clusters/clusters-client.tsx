"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { LayersIcon, PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

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
  LIST_CLUSTERS,
  UNREGISTER_TENANT_CLUSTER,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

import { RegisterClusterDialog } from "./register-cluster-dialog";

interface Resp {
  astroliftClusters: AstroliftTenantCluster[];
}

export function ClustersClient() {
  const [open, setOpen] = React.useState(false);
  const { data, loading } = useQuery<Resp>(LIST_CLUSTERS);
  const [unregister, { loading: deleting }] = useMutation<{
    unregisterTenantCluster: MutationResult<{ id: string; deleted: boolean }>;
  }>(UNREGISTER_TENANT_CLUSTER, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });

  async function handleUnregister(c: AstroliftTenantCluster) {
    if (
      !confirm(
        `Unregister cluster ${c.slug}? Refused if any active app still targets it.`,
      )
    ) {
      return;
    }
    const { data } = await unregister({ variables: { input: { id: c.id } } });
    if (data?.unregisterTenantCluster.ok) {
      toast.success(`Unregistered ${c.slug}`);
    } else {
      toast.error(
        data?.unregisterTenantCluster.errors?.[0]?.message ?? "Failed",
      );
    }
  }

  const list = data?.astroliftClusters ?? [];

  return (
    <PageShell
      title="Clusters"
      description="Tenant Kubernetes clusters registered with the platform. The control plane probes capabilities on register and caches the result."
      actions={
        <Button onClick={() => setOpen(true)}>
          <PlusIcon className="size-4" />
          Register cluster
        </Button>
      }
    >
      <Card>
        <CardContent className="p-0">
          {loading ? (
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
                  <TableHead>Provider</TableHead>
                  <TableHead>Region</TableHead>
                  <TableHead>Auth</TableHead>
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
                      <div className="font-medium">{c.name}</div>
                      <div className="text-muted-foreground font-mono text-xs">
                        {c.slug}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{c.providerPluginSlug}</Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {c.region || "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {c.authMethod}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {c.ingressClass}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {c.capabilitiesProbedAt
                        ? new Date(c.capabilitiesProbedAt).toLocaleString()
                        : "never"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleUnregister(c)}
                        disabled={deleting}
                      >
                        <Trash2Icon className="size-4" />
                        <span className="sr-only">Unregister</span>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <RegisterClusterDialog open={open} onOpenChange={setOpen} />
    </PageShell>
  );
}
