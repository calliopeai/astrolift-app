"use client";

import { useQuery } from "@apollo/client/react";
import { LayersIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
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
import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

interface Resp {
  astroliftClusters: AstroliftTenantCluster[];
}

export default function ClustersPage() {
  const { data, loading } = useQuery<Resp>(LIST_CLUSTERS);
  const list = data?.astroliftClusters ?? [];

  return (
    <PageShell
      title="Clusters"
      description="Tenant Kubernetes clusters registered with the platform. The control plane probes capabilities on register and caches the result."
    >
      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<LayersIcon className="size-5" />}
                title="No clusters registered"
                description="Run the install playbook for your cloud (astrolift-opscode/INSTALL-<cloud>.md) to register one."
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
                    <TableCell className="font-mono text-xs">{c.region || "—"}</TableCell>
                    <TableCell className="font-mono text-xs">{c.authMethod}</TableCell>
                    <TableCell className="font-mono text-xs">{c.ingressClass}</TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {c.capabilitiesProbedAt
                        ? new Date(c.capabilitiesProbedAt).toLocaleString()
                        : "never"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </PageShell>
  );
}
