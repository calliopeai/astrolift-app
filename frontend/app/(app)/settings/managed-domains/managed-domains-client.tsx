"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckIcon,
  GlobeIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { useListControls } from "@/hooks/use-list-controls";
import { PageShell } from "@/components/PageShell";
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
import { SOFT_DELETE_MANAGED_DOMAIN } from "@/graphql/clusters/managed-domains.mutations";
import { LIST_MANAGED_DOMAINS } from "@/graphql/clusters/managed-domains.queries";
import type { AstroliftManagedDomain } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { AddManagedDomainDialog } from "./add-managed-domain-dialog";

interface Resp {
  astroliftManagedDomains: AstroliftManagedDomain[];
}

const DRIVER_LABEL: Record<string, string> = {
  route53: "AWS Route 53",
  cloud_dns: "Google Cloud DNS",
  azure_dns: "Azure DNS",
};

const DEFAULT_FOR_LABEL: Record<string, string> = {
  tenant_apps: "Tenant apps",
  preview_envs: "Preview envs",
  both: "Both",
  none: "None",
};

const DEFAULT_FOR_TONE: Record<string, string> = {
  tenant_apps: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  preview_envs: "bg-purple-500/15 text-purple-700 dark:text-purple-300",
  both: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  none: "bg-zinc-500/15 text-zinc-700 dark:text-zinc-300",
};

export function ManagedDomainsClient() {
  const fmt = useFormatters();
  const { can } = useMyPermissions();
  const canConfigure = can("provider_plugin.configure");

  const { data, loading, error } = useQuery<Resp>(LIST_MANAGED_DOMAINS);

  const [addOpen, setAddOpen] = React.useState(false);
  const [deleteTarget, setDeleteTarget] =
    React.useState<AstroliftManagedDomain | null>(null);

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeleteManagedDomain: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_MANAGED_DOMAIN, {
    refetchQueries: [{ query: LIST_MANAGED_DOMAINS }],
    awaitRefetchQueries: true,
  });

  async function handleDelete(domain: AstroliftManagedDomain) {
    const { data } = await softDelete({
      variables: { input: { id: domain.id } },
    });
    if (data?.softDeleteManagedDomain.ok) {
      toast.success(`Deleted ${domain.zone}`);
    } else {
      throw new Error(
        data?.softDeleteManagedDomain.errors?.[0]?.message ?? "Failed"
      );
    }
  }

  const allDomains = data?.astroliftManagedDomains ?? [];
  const ctrl = useListControls({
    data: allDomains,
    searchFn: (d) => [d.zone, d.dnsDriver, d.defaultFor].join(" "),
    initialPageSize: 25,
    sortFn: (a, b, s) => {
      const dir = s.dir === "asc" ? 1 : -1;
      if (s.key === "zone") return a.zone.localeCompare(b.zone) * dir;
      if (s.key === "driver") return a.dnsDriver.localeCompare(b.dnsDriver) * dir;
      return 0;
    },
  });
  const list = ctrl.rows;

  return (
    <PageShell
      title="Managed domains"
      description="DNS zones the platform manages for tenant apps and preview environments. Hostnames under these zones get records created and updated automatically by the bound DNS driver."
      actions={
        <Can permission="provider_plugin.configure">
          <Button onClick={() => setAddOpen(true)}>
            <PlusIcon className="size-4" />
            Add domain
          </Button>
        </Can>
      }
    >
      {error && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardHeader className="flex flex-row items-start gap-3 space-y-0 pb-3">
            <AlertTriangleIcon className="text-destructive mt-0.5 size-4" />
            <div className="flex-1">
              <CardTitle className="text-destructive text-sm">
                Couldn&apos;t load managed domains
              </CardTitle>
              <CardDescription>{error.message}</CardDescription>
            </div>
          </CardHeader>
        </Card>
      )}

      {allDomains.length > 0 && (
        <ListControls controls={ctrl} searchPlaceholder="Search domains…" className="mb-3" />
      )}
      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 && !error ? (
            <div className="p-6">
              <EmptyState
                icon={<GlobeIcon className="size-5" />}
                title="No managed domains configured"
                description="Add a DNS zone to enable automatic hostname provisioning for tenant apps."
              />
            </div>
          ) : list.length === 0 ? null : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead><SortableHeader sortKey="zone" sort={ctrl.sort} onToggle={ctrl.toggleSort}>Zone</SortableHeader></TableHead>
                  <TableHead>Driver</TableHead>
                  <TableHead>Default for</TableHead>
                  <TableHead>Wildcard</TableHead>
                  <TableHead>Added</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell>
                      <div className="font-mono text-sm">{d.zone}</div>
                      <div className="mt-1">
                        {d.organizationSlug ? (
                          <Badge variant="outline" className="text-muted-foreground text-[10px]">
                            {d.organizationSlug}
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="text-muted-foreground text-[10px]">
                            Platform
                          </Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">
                        {DRIVER_LABEL[d.dnsDriver] ?? d.dnsDriver}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge
                        className={DEFAULT_FOR_TONE[d.defaultFor]}
                        variant="secondary"
                      >
                        {DEFAULT_FOR_LABEL[d.defaultFor] ?? d.defaultFor}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {d.isWildcardManaged ? (
                        <Badge
                          className="gap-1 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                          variant="secondary"
                        >
                          <CheckIcon className="size-3" />
                          managed
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground text-xs">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {fmt.formatDate(d.createdAt)}
                    </TableCell>
                    <TableCell className="text-right">
                      {canConfigure ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setDeleteTarget(d)}
                          disabled={deleting}
                        >
                          <Trash2Icon className="size-4" />
                          <span className="sr-only">Delete</span>
                        </Button>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <AddManagedDomainDialog open={addOpen} onOpenChange={setAddOpen} />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={
          deleteTarget
            ? `Delete managed domain ${deleteTarget.zone}?`
            : "Delete managed domain?"
        }
        description="Existing apps using hostnames in this zone keep their current DNS records, but new records will no longer be created or updated. Re-add the zone to resume management."
        confirmLabel="Delete domain"
        destructive
        onConfirm={async () => {
          if (deleteTarget) await handleDelete(deleteTarget);
        }}
      />
    </PageShell>
  );
}
