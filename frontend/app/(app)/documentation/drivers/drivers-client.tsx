"use client";

import { useQuery } from "@apollo/client/react";
import { CheckIcon, MinusIcon, PlugIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
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
import {
  LIST_CLUSTERS,
  LIST_PROVIDER_PLUGINS,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftProviderPlugin } from "@/graphql/clusters/clusters.types";

interface Resp {
  astroliftProviderPlugins: AstroliftProviderPlugin[];
}

interface ClusterCountResp {
  astroliftClusters: Array<{ id: string; providerPluginSlug: string }>;
}

// Canonical driver-capability axes. Order matters — these are the
// columns of the matrix. Mirrors the kinds enumerated in
// astrolift-providers / docs/reference/providers.md.
const CAPABILITIES = [
  { key: "cluster", label: "Cluster" },
  { key: "ingress", label: "Ingress" },
  { key: "dns", label: "DNS" },
  { key: "tls", label: "TLS" },
  { key: "secrets", label: "Secrets" },
  { key: "identity", label: "Identity" },
  { key: "registry", label: "Registry" },
] as const;

// Known managed-service kinds the platform supports. Each provider
// advertises a subset via its capabilities manifest.
const MANAGED_SERVICE_KINDS = [
  "postgres",
  "redis",
  "object_storage",
  "kafka",
  "search",
] as const;

// Static fallback for the four known providers when no plugins are
// registered yet (fresh install / docs preview). The live query
// (`astroliftProviderPlugins`) supersedes this when it returns rows.
const FALLBACK_PROVIDERS: ReadonlyArray<{
  slug: string;
  name: string;
  capabilities: Record<string, boolean>;
  managedServices: string[];
}> = [
  {
    slug: "aws",
    name: "AWS",
    capabilities: {
      cluster: true,
      ingress: true,
      dns: true,
      tls: true,
      secrets: true,
      identity: true,
      registry: true,
    },
    managedServices: ["postgres", "redis", "object_storage", "kafka", "search"],
  },
  {
    slug: "gcp",
    name: "Google Cloud",
    capabilities: {
      cluster: true,
      ingress: true,
      dns: true,
      tls: true,
      secrets: true,
      identity: true,
      registry: true,
    },
    managedServices: ["postgres", "redis", "object_storage"],
  },
  {
    slug: "azure",
    name: "Azure",
    capabilities: {
      cluster: true,
      ingress: true,
      dns: true,
      tls: true,
      secrets: true,
      identity: true,
      registry: true,
    },
    managedServices: ["postgres", "redis", "object_storage"],
  },
  {
    slug: "k8s_native",
    name: "Kubernetes-native",
    capabilities: {
      cluster: true,
      ingress: true,
      dns: false,
      tls: true,
      secrets: true,
      identity: false,
      registry: false,
    },
    managedServices: [],
  },
];

// The capabilities manifest is loosely-typed JSON on the GraphQL side
// (Scalars['JSON']). Normalize it into the row shape the UI renders.
interface DriverRow {
  slug: string;
  name: string;
  version?: string;
  isEnabled?: boolean;
  capabilities: Record<string, boolean>;
  managedServices: string[];
}

function normalize(plugin: AstroliftProviderPlugin): DriverRow {
  const manifest = (plugin.capabilitiesManifest ?? {}) as Record<
    string,
    unknown
  >;
  // The backend's bootstrap_provider_plugins writes
  //   { drivers: ["cluster","dns",…], managed_service_kinds: ["postgres",…] }
  // (drivers is an ARRAY of role strings, not a dict).  Older / hand-edited
  // manifests may use a dict shape, so accept both: build a Set of role
  // names from whichever form is present.
  const rawDrivers = manifest.drivers;
  const driverRoles = new Set<string>();
  if (Array.isArray(rawDrivers)) {
    for (const r of rawDrivers) if (typeof r === "string") driverRoles.add(r);
  } else if (rawDrivers && typeof rawDrivers === "object") {
    for (const [k, v] of Object.entries(rawDrivers)) {
      if (v) driverRoles.add(k);
    }
  }

  const capabilities: Record<string, boolean> = {};
  for (const cap of CAPABILITIES) {
    capabilities[cap.key] = Boolean(
      driverRoles.has(cap.key) ||
        manifest[cap.key] ||
        manifest[`${cap.key}_driver`],
    );
  }

  // Backend canonical key is ``managed_service_kinds`` (list of kind
  // strings).  Fall back to legacy ``managed_services`` / camelCase
  // shapes so a hand-edited row still renders.
  const managedRaw =
    (manifest.managed_service_kinds as unknown[] | undefined) ??
    (manifest.managedServiceKinds as unknown[] | undefined) ??
    (manifest.managed_services as unknown[] | undefined) ??
    (manifest.managedServices as unknown[] | undefined) ??
    [];
  const managedServices = Array.isArray(managedRaw)
    ? managedRaw
        .map((s) =>
          typeof s === "string"
            ? s
            : typeof s === "object" && s !== null && "kind" in s
              ? String((s as { kind: unknown }).kind)
              : null,
        )
        .filter((s): s is string => Boolean(s))
    : [];

  return {
    slug: plugin.slug,
    name: plugin.name,
    version: plugin.version,
    isEnabled: plugin.isEnabled,
    capabilities,
    managedServices,
  };
}

export function DriversClient() {
  const { data, loading } = useQuery<Resp>(LIST_PROVIDER_PLUGINS, {
    fetchPolicy: "cache-first",
  });
  const { data: clusterData } = useQuery<ClusterCountResp>(LIST_CLUSTERS, {
    fetchPolicy: "cache-first",
  });

  const rows: DriverRow[] = React.useMemo(() => {
    const live = data?.astroliftProviderPlugins ?? [];
    if (live.length > 0) return live.map(normalize);
    return FALLBACK_PROVIDERS.map((p) => ({ ...p }));
  }, [data]);

  // Bound-cluster count per provider slug. Drives the inline 'N
  // clusters bound' affordance on each provider row — lets an operator
  // scan the page and spot dormant drivers at a glance.
  const clusterCountByProvider = React.useMemo(() => {
    const counts = new Map<string, number>();
    for (const c of clusterData?.astroliftClusters ?? []) {
      counts.set(
        c.providerPluginSlug,
        (counts.get(c.providerPluginSlug) ?? 0) + 1,
      );
    }
    return counts;
  }, [clusterData]);

  // Union of managed-service kinds declared across all live rows.
  // Keeps the table from going stale when the platform ships a new
  // kind (e.g. vector_index, time_series) — the column appears as
  // soon as any provider advertises it.  Falls back to the hardcoded
  // taxonomy when no rows are live.
  const managedKindColumns = React.useMemo(() => {
    const seen = new Set<string>();
    for (const r of rows) for (const k of r.managedServices) seen.add(k);
    if (seen.size === 0) return [...MANAGED_SERVICE_KINDS] as string[];
    return Array.from(seen).sort();
  }, [rows]);

  const usingFallback =
    !loading && (data?.astroliftProviderPlugins?.length ?? 0) === 0;

  return (
    <PageShell
      title="Driver reference"
      description="Provider plugins and the per-capability driver implementations they advertise. The matrix below is sourced from the registered ProviderPlugin manifests; when no plugins are live, a static known-provider list is shown."
    >
      {loading ? (
        <Card>
          <CardContent className="p-6">
            <Skeleton className="h-44 w-full" />
          </CardContent>
        </Card>
      ) : (
        <>
          {usingFallback && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Static reference</CardTitle>
                <CardDescription>
                  No provider plugins are registered on this install yet. The
                  table below shows the canonical capability surface for each
                  supported provider. Once plugins load, this surface switches
                  to the live manifest.
                </CardDescription>
              </CardHeader>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <PlugIcon className="size-4" />
                Capability matrix
              </CardTitle>
              <CardDescription>
                Each row is a provider; columns are the driver kinds it
                bundles. A check means the provider implements that driver.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[260px]">Provider</TableHead>
                    {CAPABILITIES.map((cap) => (
                      <TableHead key={cap.key} className="text-center">
                        {cap.label}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => {
                    const boundCount =
                      clusterCountByProvider.get(row.slug) ?? 0;
                    return (
                      <TableRow key={row.slug}>
                        <TableCell>
                          <div className="flex flex-col gap-1">
                            <span className="text-sm font-medium">
                              {row.name}
                            </span>
                            <span className="text-muted-foreground font-mono text-xs">
                              {row.slug}
                              {row.version ? ` · v${row.version}` : ""}
                            </span>
                            {boundCount > 0 ? (
                              <Link
                                href="/clusters"
                                className="inline-flex w-fit"
                              >
                                <Badge
                                  variant="secondary"
                                  className="text-2xs"
                                >
                                  {boundCount}{" "}
                                  {boundCount === 1
                                    ? "cluster bound"
                                    : "clusters bound"}
                                </Badge>
                              </Link>
                            ) : (
                              <span className="text-muted-foreground text-2xs">
                                No clusters
                              </span>
                            )}
                          </div>
                        </TableCell>
                        {CAPABILITIES.map((cap) => (
                          <TableCell key={cap.key} className="text-center">
                            {row.capabilities[cap.key] ? (
                              <CheckIcon
                                className="text-success-fg mx-auto size-4"
                                aria-label="supported"
                              />
                            ) : (
                              <MinusIcon
                                className="text-muted-foreground mx-auto size-4"
                                aria-label="not supported"
                              />
                            )}
                          </TableCell>
                        ))}
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
              <p className="text-muted-foreground mt-4 text-xs">
                Matrix reflects manifest declarations · last probed varies by
                cluster ·{" "}
                <Link
                  href="/clusters"
                  className="text-primary hover:underline"
                >
                  See live capabilities →
                </Link>
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Managed services per provider
              </CardTitle>
              <CardDescription>
                Which managed-service kinds each provider can provision.
                Bindings declared in <code>[[managed_services]]</code> on the
                app manifest must match one of the kinds listed here.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[200px]">Provider</TableHead>
                    {managedKindColumns.map((kind) => (
                      <TableHead key={kind} className="text-center">
                        {kind}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.slug}>
                      <TableCell>
                        <span className="text-sm font-medium">{row.name}</span>
                      </TableCell>
                      {managedKindColumns.map((kind) => (
                        <TableCell key={kind} className="text-center">
                          {row.managedServices.includes(kind) ? (
                            <Badge className="text-2xs">yes</Badge>
                          ) : (
                            <span className="text-muted-foreground text-xs">
                              —
                            </span>
                          )}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </PageShell>
  );
}
