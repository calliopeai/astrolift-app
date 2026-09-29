"use client";

import { CopyIcon, GlobeIcon, PlusIcon, RefreshCwIcon, Trash2Icon } from "lucide-react";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { adminCrumbs } from "@/components/screens/administration/insights/header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
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
import type { AstroliftManagedDomain } from "@/graphql/clusters/clusters.types";
import { useFormatters } from "@/lib/i18n/formatters";

import type { ManagedDomainsState } from "./use-managed-domains";

export type ManagedDomainsScreenProps = ManagedDomainsState;

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

/**
 * Admin › Domains (spec 44 §5.1): the org's managed DNS zones on the shared
 * list (views All · Mine · Not active, status, driver and default-for
 * filters, numbered pages), revalidate and delete in each row's `⋯`, the
 * Add zone sheet. Data and mutations come from useManagedDomains; this
 * holds only UI state (the sheet, its form, the delete target).
 */
export function ManagedDomainsScreen({
  list,
  rows,
  totalCount,
  loading,
  error,
  onRetry,
  creating,
  deleting,
  revalidating,
  onCreate,
  onDelete,
  onRevalidate,
  onCopyNameservers,
}: ManagedDomainsScreenProps) {
  const fmt = useFormatters();
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

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (await onCreate({ zone, dnsDriver, defaultFor, wildcard })) setCreateOpen(false);
  }

  const columns: Column<AstroliftManagedDomain>[] = [
    {
      id: "zone",
      header: "Zone",
      sortKey: "zone",
      cellClassName: "max-w-72",
      cell: (d) => (
        <span className="block truncate font-mono" title={d.zone}>
          {d.zone}
        </span>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (d) => (
        <Badge className={provisionBadge(d.provisionState).className} variant="secondary">
          {provisionBadge(d.provisionState).label}
        </Badge>
      ),
    },
    {
      id: "nameservers",
      header: "Nameservers (point your registrar here)",
      label: "Nameservers",
      cellClassName: "relative z-10 max-w-80",
      cell: (d) =>
        d.provisionNameservers.length > 0 ? (
          <div className="flex min-w-0 items-start gap-2">
            <div className="min-w-0 font-mono text-xs leading-5">
              {d.provisionNameservers.map((ns) => (
                <div key={ns} className="truncate" title={ns}>
                  {ns}
                </div>
              ))}
            </div>
            <Button
              size="sm"
              variant="ghost"
              aria-label={`Copy nameservers for ${d.zone}`}
              onClick={() => onCopyNameservers(d)}
            >
              <CopyIcon className="size-3.5" />
            </Button>
          </div>
        ) : (
          <span className="text-muted-foreground text-xs">
            {d.provisionState === "" ? "—" : "pending…"}
          </span>
        ),
    },
    {
      id: "driver",
      header: "DNS driver",
      sortKey: "driver",
      cell: (d) => <Badge variant="outline">{d.dnsDriver}</Badge>,
    },
    {
      id: "defaultFor",
      header: "Default for",
      cell: (d) => (
        <Badge className={defaultForBadge[d.defaultFor]} variant="secondary">
          {d.defaultFor.replace(/_/g, " ")}
        </Badge>
      ),
    },
    {
      id: "wildcard",
      header: "Wildcard",
      cell: (d) => (d.isWildcardManaged ? "yes" : "no"),
    },
    {
      id: "created",
      header: "Created",
      sortKey: "created",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (d) => fmt.formatDate(d.createdAt),
    },
  ];

  return (
    <>
      <ListPage<AstroliftManagedDomain>
        header={{
          crumbs: adminCrumbs("domains", "Domains"),
          title: "Managed domains",
          context:
            "Org-level DNS zones the platform manages for apps and preview environments. Per-app hostnames are configured on each app's Domains tab.",
          primaryAction: (
            <Button onClick={() => setCreateOpen(true)}>
              <PlusIcon className="size-4" />
              Add zone
            </Button>
          ),
        }}
        list={list}
        label="Managed domains"
        columns={columns}
        rows={rows}
        getRowId={(d) => d.id}
        rowHref={(d) => `/domains/${d.id}`}
        rowActions={(d) => (
          <>
            <DropdownMenuItem
              disabled={revalidating || !d.provisionClusterId}
              onSelect={() => void onRevalidate(d)}
            >
              <RefreshCwIcon className="size-4" />
              Revalidate DNS
            </DropdownMenuItem>
            <DropdownMenuItem
              variant="destructive"
              disabled={deleting}
              onSelect={() => setDeleteTarget(d)}
            >
              <Trash2Icon className="size-4" />
              Delete
            </DropdownMenuItem>
          </>
        )}
        loading={loading}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        empty={{
          icon: <GlobeIcon className="size-5" />,
          title: "No managed domains",
          description:
            "Bind a DNS zone to a DnsDriver. The install playbook seeds one zone per cloud profile.",
          learnMoreHref: "/documentation/custom-domains",
          learnMoreLabel: "Custom domains",
        }}
      />

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
          if (deleteTarget) await onDelete(deleteTarget);
        }}
      />
    </>
  );
}
