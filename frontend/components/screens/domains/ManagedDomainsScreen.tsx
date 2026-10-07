"use client";

import { CopyIcon, GlobeIcon, PlusIcon } from "lucide-react";
import * as React from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

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

export type ManagedDomainsScreenProps = ManagedDomainsState & {
  /**
   * Why Astrolift writes no DNS on this install (DNS withheld,
   * calliope-installer#447): a route53 zone, which the platform writes
   * itself, cannot be added.
   */
  dnsRestriction?: string | null;
};

const defaultForBadge: Record<string, string> = {
  tenant_apps: "bg-info/15 text-info-fg",
  preview_envs: "bg-chart-3/15 text-chart-3",
  both: "bg-info/15 text-info-fg",
  none: "bg-foreground/5 text-muted-foreground",
};

function provisionBadge(state: string): { label: string; className: string } {
  if (state === "mark_active") {
    return { label: "provisioned", className: "bg-info/15 text-info-fg" };
  }
  if (state === "") {
    return { label: "unprovisioned", className: "bg-foreground/5 text-muted-foreground" };
  }
  return { label: "provisioning", className: "bg-info/15 text-info-fg" };
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
  canCreate,
  onCreate,
  onCopyNameservers,
  dnsRestriction,
}: ManagedDomainsScreenProps) {
  const t = useTranslations("managedDomains");
  const connectionText = useTranslations("domainConnections");
  const fmt = useFormatters();
  const [createOpen, setCreateOpen] = React.useState(false);
  const [zone, setZone] = React.useState("");
  const [dnsDriver, setDnsDriver] = React.useState("route53");
  const [defaultFor, setDefaultFor] = React.useState("none");
  const [wildcard, setWildcard] = React.useState(false);
  const withheld = Boolean(dnsRestriction && dnsDriver === "route53");

  function changeCreateOpen(next: boolean) {
    if (!next) {
      setZone("");
      setDnsDriver("route53");
      setDefaultFor("none");
      setWildcard(false);
    }
    setCreateOpen(next);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (await onCreate({ zone, dnsDriver, defaultFor, wildcard })) changeCreateOpen(false);
  }

  const columns: Column<AstroliftManagedDomain>[] = [
    {
      id: "zone",
      header: t("zone"),
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
      header: t("configuration"),
      cell: (d) => (
        <Badge className={provisionBadge(d.provisionState).className} variant="secondary">
          {t(provisionBadge(d.provisionState).label)}
        </Badge>
      ),
    },
    {
      id: "nameservers",
      header: t("nameservers"),
      label: t("nameservers"),
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
              aria-label={`${t("copyNameservers")} ${d.zone}`}
              onClick={() => onCopyNameservers(d)}
            >
              <CopyIcon className="size-3.5" />
            </Button>
          </div>
        ) : (
          <span className="text-muted-foreground text-xs">
            {d.provisionState === "" ? "—" : t("provisioning")}
          </span>
        ),
    },
    {
      id: "driver",
      header: t("driver"),
      sortKey: "driver",
      cell: (d) => <Badge variant="outline">{d.dnsDriver}</Badge>,
    },
    {
      id: "defaultFor",
      header: t("defaultFor"),
      cell: (d) => (
        <Badge className={defaultForBadge[d.defaultFor]} variant="secondary">
          {d.defaultFor.replace(/_/g, " ")}
        </Badge>
      ),
    },
    {
      id: "wildcard",
      header: t("wildcard"),
      cell: (d) => (d.isWildcardManaged ? t("yes") : t("no")),
    },
    {
      id: "created",
      header: t("created"),
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
          title: t("title"),
          context: `${t("listHelp")} ${t("listBounded")}`,
          primaryAction: (
            <div className="flex flex-wrap gap-2">
              <Button asChild variant="outline">
                <Link href="/domains/connect">{connectionText("title")}</Link>
              </Button>
              <Button
                disabled={!canCreate || loading || Boolean(error)}
                onClick={() => setCreateOpen(true)}
              >
                <PlusIcon className="size-4" />
                {t("add")}
              </Button>
            </div>
          ),
        }}
        list={list}
        label={t("title")}
        columns={columns}
        rows={rows}
        getRowId={(d) => d.id}
        rowHref={(d) => `/domains/${d.id}`}
        rowActions={(d) => (
          <DropdownMenuItem asChild>
            <Link href={`/domains/${d.id}`}>{t("open")}</Link>
          </DropdownMenuItem>
        )}
        loading={loading}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        empty={{
          icon: <GlobeIcon className="size-5" />,
          title: t("listEmpty"),
          description: t("listEmptyHelp"),
          learnMoreHref: "/documentation/custom-domains",
          learnMoreLabel: "Custom domains",
        }}
      />

      <Sheet open={createOpen} onOpenChange={changeCreateOpen}>
        <SheetContent className="flex flex-col">
          <SheetHeader>
            <SheetTitle>{t("createTitle")}</SheetTitle>
            <SheetDescription>{t("createHelp")}</SheetDescription>
          </SheetHeader>
          <form onSubmit={handleCreate} className="flex flex-1 flex-col gap-4 px-4 pb-4">
            <div className="space-y-2">
              <Label htmlFor="zone">{t("zoneRoot")}</Label>
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
                <Label htmlFor="dns-driver">{t("driver")}</Label>
                {/* A Select, not free text (#918): a typo'd driver binds the
                    zone to a nonexistent driver that only fails later. */}
                <Select value={dnsDriver} onValueChange={setDnsDriver}>
                  <SelectTrigger id="dns-driver" className="font-mono text-xs">
                    <SelectValue placeholder={t("selectDriver")} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="route53">route53</SelectItem>
                    <SelectItem value="cloud_dns">cloud_dns</SelectItem>
                    <SelectItem value="external_dns">external_dns</SelectItem>
                  </SelectContent>
                </Select>
                {withheld && (
                  <p id="md-withheld" className="text-warning-fg text-xs">
                    {dnsRestriction}
                  </p>
                )}
              </div>
              <div className="space-y-2">
                <Label htmlFor="default-for">{t("defaultFor")}</Label>
                <Select value={defaultFor} onValueChange={setDefaultFor}>
                  <SelectTrigger id="default-for">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">{t("none")}</SelectItem>
                    <SelectItem value="tenant_apps">{t("tenantApps")}</SelectItem>
                    <SelectItem value="preview_envs">{t("previewEnvironments")}</SelectItem>
                    <SelectItem value="both">{t("both")}</SelectItem>
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
              {t("wildcardHelp")}
            </label>
            <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
              <Button type="button" variant="outline" onClick={() => changeCreateOpen(false)}>
                {t("cancel")}
              </Button>
              <Button
                type="submit"
                disabled={creating || !zone || withheld}
                aria-describedby={withheld ? "md-withheld" : undefined}
              >
                {creating ? t("adding") : t("add")}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>
    </>
  );
}
