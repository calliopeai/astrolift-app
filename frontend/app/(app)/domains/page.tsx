"use client";

import { useQuery } from "@apollo/client/react";
import { GlobeIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
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
import { LIST_MANAGED_DOMAINS } from "@/graphql/clusters/clusters.queries";
import type { AstroliftManagedDomain } from "@/graphql/clusters/clusters.types";

interface Resp {
  astroliftManagedDomains: AstroliftManagedDomain[];
}

const defaultForBadge: Record<string, string> = {
  tenant_apps: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  preview_envs: "bg-purple-500/15 text-purple-700 dark:text-purple-300",
  both: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  none: "bg-zinc-500/15 text-zinc-700 dark:text-zinc-300",
};

export default function DomainsPage() {
  const { data, loading } = useQuery<Resp>(LIST_MANAGED_DOMAINS);
  const list = data?.astroliftManagedDomains ?? [];

  return (
    <PageShell
      title="Managed domains"
      description="DNS zones the platform manages on behalf of tenant apps and preview environments."
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
                icon={<GlobeIcon className="size-5" />}
                title="No managed domains"
                description="Bind a DNS zone to a DnsDriver via the install playbook or the operator API."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Zone</TableHead>
                  <TableHead>DNS driver</TableHead>
                  <TableHead>Default for</TableHead>
                  <TableHead>Wildcard</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell className="font-mono">{d.zone}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{d.dnsDriver}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge
                        className={defaultForBadge[d.defaultFor]}
                        variant="secondary"
                      >
                        {d.defaultFor.replace(/_/g, " ")}
                      </Badge>
                    </TableCell>
                    <TableCell>{d.isWildcardManaged ? "yes" : "no"}</TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {new Date(d.createdAt).toLocaleDateString()}
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
