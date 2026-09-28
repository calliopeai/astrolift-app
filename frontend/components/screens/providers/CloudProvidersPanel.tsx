"use client";

import { CheckCircle2Icon, ChevronDownIcon, ChevronRightIcon, CloudIcon } from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { ViewToggle } from "@/components/ViewToggle";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftProviderPlugin } from "@/graphql/clusters/clusters.types";
import { cn } from "@/lib/utils";

import type { useCloudProviders } from "./use-cloud-providers";

export type CloudProvidersPanelViewProps = ReturnType<typeof useCloudProviders>;

function driversOf(p: AstroliftProviderPlugin): string[] {
  return Object.keys(p.capabilitiesManifest ?? {});
}

/**
 * Cloud-provider section of the unified Providers page (#887).
 *
 * `astroliftProviderPlugins` is a catalog of every driver bundle the
 * workers loaded, regardless of whether this org uses it — which made
 * the old page imply gcp/azure/etc. were configured. There is no
 * first-class "configured for this org" flag on the plugin, so we derive
 * it: a provider counts as configured once at least one TenantCluster
 * references its slug. The default view shows only those (on an
 * AWS-only instance, just `aws`); the rest of the catalog stays one
 * click away under a disclosure, clearly marked "available" so it
 * informs without misleading.
 */
export function CloudProvidersPanelView({
  loading,
  pluginCount,
  configured,
  available,
  viewMode,
  setViewMode,
}: CloudProvidersPanelViewProps) {
  const [showAvailable, setShowAvailable] = React.useState(false);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Cloud providers</h2>
          <p className="text-muted-foreground mt-1 max-w-2xl text-sm">
            Driver bundles wired to this organization — a provider shows as configured once it backs
            a registered cluster (ClusterDriver, IngressDriver, DnsDriver, and so on). Register a
            cluster on a new cloud to light one up.
          </p>
        </div>
        {pluginCount > 0 && <ViewToggle mode={viewMode} onChange={setViewMode} />}
      </div>

      {loading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
        </div>
      ) : (
        <>
          {configured.length === 0 ? (
            <Card>
              <CardContent className="p-6">
                <EmptyState
                  icon={<CloudIcon className="size-5" />}
                  title="No cloud providers configured"
                  description="Register a cluster to bind a provider plugin to this org. The provider that backs it — aws, gcp, azure, or a self-managed Kubernetes endpoint — then shows up here with its wired capabilities."
                />
              </CardContent>
            </Card>
          ) : viewMode === "list" ? (
            <div className="flex flex-col gap-1">
              {configured.map(({ plugin, clusters }) => (
                <ProviderRow key={plugin.id} plugin={plugin} clusters={clusters} />
              ))}
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {configured.map(({ plugin, clusters }) => (
                <ProviderCard key={plugin.id} plugin={plugin} clusters={clusters} />
              ))}
            </div>
          )}

          {available.length > 0 && (
            <div className="flex flex-col gap-3">
              <button
                type="button"
                onClick={() => setShowAvailable((v) => !v)}
                className="text-muted-foreground hover:text-foreground inline-flex w-fit items-center gap-1.5 text-sm"
                aria-expanded={showAvailable}
              >
                {showAvailable ? (
                  <ChevronDownIcon className="size-4" />
                ) : (
                  <ChevronRightIcon className="size-4" />
                )}
                {showAvailable ? "Hide" : "Show"} {available.length} available provider
                {available.length === 1 ? "" : "s"}
              </button>

              {showAvailable &&
                (viewMode === "list" ? (
                  <div className="flex flex-col gap-1">
                    {available.map((p) => (
                      <ProviderRow key={p.id} plugin={p} />
                    ))}
                  </div>
                ) : (
                  <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                    {available.map((p) => (
                      <ProviderCard key={p.id} plugin={p} />
                    ))}
                  </div>
                ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/**
 * One provider as a card. `clusters` is undefined for catalog entries
 * the org hasn't configured yet — those render de-emphasized with an
 * "available" badge instead of the configured/cluster-count chrome.
 */
function ProviderCard({
  plugin,
  clusters,
}: {
  plugin: AstroliftProviderPlugin;
  clusters?: number;
}) {
  const drivers = driversOf(plugin);
  const isConfigured = clusters !== undefined;
  return (
    <Card className={isConfigured ? undefined : "border-dashed"}>
      <CardHeader className="flex flex-row items-start gap-3 space-y-0">
        <div
          className={cn(
            "rounded-md p-2",
            isConfigured ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"
          )}
        >
          <CloudIcon className="size-4" />
        </div>
        <div className="flex-1">
          <CardTitle className="text-base">{plugin.name}</CardTitle>
          <CardDescription className="font-mono text-xs">
            {plugin.slug} · v{plugin.version}
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-2">
          {isConfigured ? (
            <Badge className="bg-success/15 text-success-fg gap-1" variant="secondary">
              <CheckCircle2Icon className="size-3" />
              configured
            </Badge>
          ) : (
            <Badge variant="outline">available</Badge>
          )}
          <span className="text-muted-foreground text-xs">
            {isConfigured ? `${clusters} cluster${clusters === 1 ? "" : "s"} · ` : ""}
            {drivers.length} driver{drivers.length === 1 ? "" : "s"}
          </span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          {drivers.slice(0, 8).map((d) => (
            <Badge key={d} variant="outline" className="text-xs">
              {d}
            </Badge>
          ))}
          {drivers.length > 8 && (
            <Badge variant="secondary" className="text-xs">
              +{drivers.length - 8}
            </Badge>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/** Compact list-view row counterpart to {@link ProviderCard}. */
function ProviderRow({ plugin, clusters }: { plugin: AstroliftProviderPlugin; clusters?: number }) {
  const drivers = driversOf(plugin);
  const isConfigured = clusters !== undefined;
  return (
    <div className="hover:bg-accent/50 flex items-center gap-3 rounded-md border px-4 py-2.5 transition-colors">
      {isConfigured ? (
        <CheckCircle2Icon className="text-success-fg size-4 shrink-0" />
      ) : (
        <CloudIcon className="text-muted-foreground size-4 shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        <span className="font-medium">{plugin.name}</span>
        <span className="text-muted-foreground ml-2 font-mono text-xs">
          {plugin.slug} · v{plugin.version}
        </span>
      </div>
      {isConfigured ? (
        <Badge variant="secondary" className="shrink-0 text-xs">
          {clusters} cluster{clusters === 1 ? "" : "s"}
        </Badge>
      ) : (
        <Badge variant="outline" className="shrink-0 text-xs">
          available
        </Badge>
      )}
      <Badge variant="outline" className="shrink-0 text-xs">
        {drivers.length} driver{drivers.length === 1 ? "" : "s"}
      </Badge>
    </div>
  );
}
