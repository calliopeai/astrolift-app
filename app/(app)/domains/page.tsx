"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { GlobeIcon, PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

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
  const [createOpen, setCreateOpen] = React.useState(false);
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
      toast.success(`Created ${zone}`);
      setCreateOpen(false);
    } else {
      toast.error(data?.createManagedDomain.errors?.[0]?.message ?? "Failed");
    }
  }

  async function handleDelete(d: AstroliftManagedDomain) {
    if (!confirm(`Delete managed domain ${d.zone}?`)) return;
    const { data } = await softDelete({ variables: { input: { id: d.id } } });
    if (data?.softDeleteManagedDomain.ok) {
      toast.success(`Deleted ${d.zone}`);
    } else {
      toast.error(data?.softDeleteManagedDomain.errors?.[0]?.message ?? "Failed");
    }
  }

  const list = data?.astroliftManagedDomains ?? [];

  return (
    <PageShell
      title="Managed domains"
      description="DNS zones the platform manages on behalf of tenant apps and preview environments."
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
                  <TableHead className="text-right">Actions</TableHead>
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
                      <Badge className={defaultForBadge[d.defaultFor]} variant="secondary">
                        {d.defaultFor.replace(/_/g, " ")}
                      </Badge>
                    </TableCell>
                    <TableCell>{d.isWildcardManaged ? "yes" : "no"}</TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {new Date(d.createdAt).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleDelete(d)}
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
              Bind a DNS zone to a DnsDriver. Tenant apps and previews under
              this zone get records created/updated automatically by the driver.
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
                <Input
                  id="dns-driver"
                  value={dnsDriver}
                  onChange={(e) => setDnsDriver(e.target.value)}
                  placeholder="route53 / cloud_dns / external_dns"
                  required
                  className="font-mono text-xs"
                />
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
    </PageShell>
  );
}
