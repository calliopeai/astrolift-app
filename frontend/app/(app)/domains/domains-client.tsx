"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { CopyIcon, GlobeIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
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
  CREATE_MANAGED_DOMAIN,
  LIST_MANAGED_DOMAINS,
  SOFT_DELETE_MANAGED_DOMAIN,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftManagedDomain } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

interface Resp {
  astroliftManagedDomains: AstroliftManagedDomain[];
}

const defaultForBadge: Record<string, string> = {
  tenant_apps: "bg-info/15 text-info-fg",
  preview_envs: "bg-chart-3/15 text-chart-3",
  both: "bg-success/15 text-success-fg",
  none: "bg-foreground/5 text-muted-foreground",
};

function provisionBadge(state: string): { label: string; className: string } {
  if (state === "mark_active") {
    return { label: "active", className: "bg-success/15 text-success-fg" };
  }
  if (state === "") {
    return { label: "not provisioned", className: "bg-foreground/5 text-muted-foreground" };
  }
  return { label: state.replace(/_/g, " "), className: "bg-info/15 text-info-fg" };
}

export function DomainsClient() {
  const fmt = useFormatters();
  const router = useRouter();
  const { data, loading, startPolling, stopPolling } = useQuery<Resp>(LIST_MANAGED_DOMAINS);

  // NS records land on the row a few seconds after the provisioning
  // workflow creates the zone; poll until every domain settles so the
  // operator sees them without refreshing.
  const provisioning = (data?.astroliftManagedDomains ?? []).some(
    (d) => d.provisionState !== "mark_active",
  );
  React.useEffect(() => {
    if (provisioning) startPolling(10_000);
    else stopPolling();
    return () => stopPolling();
  }, [provisioning, startPolling, stopPolling]);
  const [createOpen, setCreateOpen] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftManagedDomain | null>(null);
  const [zone, setZone] = React.useState("");
  const [dnsDriver, setDnsDriver] = React.useState("route53");
  const [defaultFor, setDefaultFor] = React.useState("none");
  const [wildcard, setWildcard] = React.useState(false);

  React.useEffect(() => {
    if (!createOpen) {
      setZone("");
      setDnsDriver("route53");
      setDefaultFor("none");
      setWildcard(false);
    }
  }, [createOpen]);

  const [createDomain, { loading: creating }] = useMutation<{
    createManagedDomain: MutationResult<AstroliftManagedDomain>;
  }>(CREATE_MANAGED_DOMAIN, {
    refetchQueries: [{ query: LIST_MANAGED_DOMAINS }],
    awaitRefetchQueries: true,
  });

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeleteManagedDomain: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_MANAGED_DOMAIN, {
    refetchQueries: [{ query: LIST_MANAGED_DOMAINS }],
    awaitRefetchQueries: true,
  });

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    const { data } = await createDomain({
      variables: {
        input: {
          zone: zone.trim(),
          dnsDriver: dnsDriver.trim(),
          defaultFor,
          isWildcardManaged: wildcard,
        },
      },
    });
    if (data?.createManagedDomain.ok) {
      toast.success(`Created ${zone} — provisioning; nameservers will appear shortly`);
      setCreateOpen(false);
    } else {
      toast.error(data?.createManagedDomain.errors?.[0]?.message ?? "Failed");
    }
  }

  async function handleDelete(d: AstroliftManagedDomain) {
    const { data } = await softDelete({ variables: { input: { id: d.id } } });
    if (data?.softDeleteManagedDomain.ok) {
      toast.success(`Deleted ${d.zone}`);
    } else {
      throw new Error(data?.softDeleteManagedDomain.errors?.[0]?.message ?? "Failed");
    }
  }

  const list = data?.astroliftManagedDomains ?? [];

  return (
    <PageShell
      title="Managed domains"
      description="Org-level DNS zones the platform manages for apps and preview environments. Per-app hostnames are configured on each app's Domains tab."
      actions={
        <Button onClick={() => setCreateOpen(true)}>
          <PlusIcon className="size-4" />
          Add zone
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
                icon={<GlobeIcon className="size-5" />}
                title="No managed domains"
                description="Bind a DNS zone to a DnsDriver. The install playbook seeds one zone per cloud profile."
                learnMoreHref="/documentation/custom-domains"
                learnMoreLabel="Custom domains"
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Zone</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Nameservers (point your registrar here)</TableHead>
                  <TableHead>DNS driver</TableHead>
                  <TableHead>Default for</TableHead>
                  <TableHead>Wildcard</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((d) => (
                  <TableRow
                    key={d.id}
                    tabIndex={0}
                    role="link"
                    aria-label={`Open managed domain ${d.zone}`}
                    onClick={() => router.push(`/domains/${d.id}`)}
                    onKeyDown={(ev) => {
                      if (ev.key === "Enter" || ev.key === " ") {
                        ev.preventDefault();
                        router.push(`/domains/${d.id}`);
                      }
                    }}
                    className="hover:bg-accent/30 focus-visible:outline-ring cursor-pointer focus-visible:outline-2 focus-visible:outline-offset-[-2px]"
                  >
                    <TableCell className="font-mono">{d.zone}</TableCell>
                    <TableCell>
                      <Badge className={provisionBadge(d.provisionState).className} variant="secondary">
                        {provisionBadge(d.provisionState).label}
                      </Badge>
                    </TableCell>
                    <TableCell onClick={(ev) => ev.stopPropagation()}>
                      {d.provisionNameservers.length > 0 ? (
                        <div className="flex items-start gap-2">
                          <div className="font-mono text-xs leading-5">
                            {d.provisionNameservers.map((ns) => (
                              <div key={ns}>{ns}</div>
                            ))}
                          </div>
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label={`Copy nameservers for ${d.zone}`}
                            onClick={async () => {
                              await navigator.clipboard.writeText(
                                d.provisionNameservers.join("\n"),
                              );
                              toast.success("Nameservers copied");
                            }}
                          >
                            <CopyIcon className="size-3.5" />
                          </Button>
                        </div>
                      ) : (
                        <span className="text-muted-foreground text-xs">
                          {d.provisionState === "" ? "—" : "pending…"}
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{d.dnsDriver}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge className={defaultForBadge[d.defaultFor]} variant="secondary">
                        {d.defaultFor.replace(/_/g, " ")}
                      </Badge>
                    </TableCell>
                    <TableCell>{d.isWildcardManaged ? "yes" : "no"}</TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {fmt.formatDate(d.createdAt)}
                    </TableCell>
                    <TableCell className="text-right" onClick={(ev) => ev.stopPropagation()}>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setDeleteTarget(d)}
                        disabled={deleting}
                      >
                        <Trash2Icon className="size-4" />
                        <span className="sr-only">Delete</span>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Sheet open={createOpen} onOpenChange={setCreateOpen}>
        <SheetContent className="flex flex-col">
          <SheetHeader>
            <SheetTitle>Add managed domain</SheetTitle>
            <SheetDescription>
              Bind a DNS zone to a DnsDriver. Tenant apps and previews under this zone get records
              created/updated automatically by the driver.
            </SheetDescription>
          </SheetHeader>
          <form onSubmit={handleCreate} className="flex flex-1 flex-col gap-4 px-4 pb-4">
            <div className="space-y-2">
              <Label htmlFor="zone">Zone (FQDN root)</Label>
              <Input
                id="zone"
                value={zone}
                onChange={(e) => setZone(e.target.value)}
                placeholder="apps.acme.example"
                required
                autoFocus
                className="font-mono text-xs"
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="dns-driver">DNS driver</Label>
                {/* A Select, not free text (#918): a typo'd driver binds the
                    zone to a nonexistent driver that only fails later. */}
                <Select value={dnsDriver} onValueChange={setDnsDriver}>
                  <SelectTrigger id="dns-driver" className="font-mono text-xs">
                    <SelectValue placeholder="Select a driver" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="route53">route53</SelectItem>
                    <SelectItem value="cloud_dns">cloud_dns</SelectItem>
                    <SelectItem value="external_dns">external_dns</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="default-for">Default for</Label>
                <Select value={defaultFor} onValueChange={setDefaultFor}>
                  <SelectTrigger id="default-for">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">none</SelectItem>
                    <SelectItem value="tenant_apps">tenant apps</SelectItem>
                    <SelectItem value="preview_envs">preview envs</SelectItem>
                    <SelectItem value="both">both</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={wildcard}
                onChange={(e) => setWildcard(e.target.checked)}
              />
              Platform owns *.{zone || "<zone>"} (wildcard)
            </label>
            <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
              <Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={creating || !zone}>
                {creating ? "Adding…" : "Add zone"}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={
          deleteTarget ? `Delete managed domain ${deleteTarget.zone}?` : "Delete managed domain?"
        }
        description="The hosted zone, its records, and the wildcard certificate are deleted from the cloud account. Apps using hostnames in this zone stop resolving. Re-add the zone to provision it again."
        confirmLabel="Delete zone"
        destructive
        onConfirm={async () => {
          if (deleteTarget) await handleDelete(deleteTarget);
        }}
      />
    </PageShell>
  );
}
