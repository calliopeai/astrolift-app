"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  Loader2Icon,
  MinusIcon,
  RotateCwIcon,
  ServerIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  LIST_CLUSTERS,
  LIST_PROVIDER_PLUGINS,
  REFRESH_CLUSTER_MANAGEMENT,
} from "@/graphql/clusters/clusters.queries";
import type {
  AstroliftProviderPlugin,
  AstroliftTenantCluster,
} from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

interface ClustersResp {
  astroliftClusters: AstroliftTenantCluster[];
}

interface PluginsResp {
  astroliftProviderPlugins: AstroliftProviderPlugin[];
}

// Same canonical capability axes as /resources/drivers — the matrix
// columns line up so an operator can compare manifest-declared vs
// live-probed capabilities side by side without re-mapping in their head.
const CAPABILITIES = [
  { key: "cluster", label: "Cluster" },
  { key: "ingress", label: "Ingress" },
  { key: "dns", label: "DNS" },
  { key: "tls", label: "TLS" },
  { key: "secrets", label: "Secrets" },
  { key: "identity", label: "Identity" },
  { key: "registry", label: "Registry" },
] as const;

type Lifecycle = "registered" | "managing" | "managed" | "decommissioning" | "error";

// Lifecycle → variant + className mapping. green for managed, amber for
// in-flight / registered (operator action needed), muted for terminal
// decommissioning, destructive for error.
function LifecycleBadge({ lifecycle }: { lifecycle: string }) {
  const lc = lifecycle as Lifecycle;
  if (lc === "managed") {
    return (
      <Badge
        variant="secondary"
        className="border-emerald-600/40 bg-emerald-600/10 text-emerald-700 dark:text-emerald-400"
      >
        Managed
      </Badge>
    );
  }
  if (lc === "managing") {
    return (
      <Badge variant="secondary" className="gap-1">
        <Loader2Icon className="size-3 animate-spin" />
        Managing
      </Badge>
    );
  }
  if (lc === "registered") {
    return (
      <Badge
        variant="secondary"
        className="border-amber-600/40 bg-amber-600/10 text-amber-700 dark:text-amber-400"
      >
        Registered
      </Badge>
    );
  }
  if (lc === "decommissioning") {
    return (
      <Badge variant="secondary" className="text-muted-foreground">
        Decommissioning
      </Badge>
    );
  }
  return <Badge variant="destructive">{lifecycle}</Badge>;
}

// Build a Map<providerSlug, Set<capabilityKey>> from the plugin
// manifest. Accepts both the canonical `{ drivers: ["cluster", ...] }`
// list shape and the legacy dict shape (same as drivers-client) so a
// hand-edited manifest still resolves.
function buildManifestCapabilities(
  plugins: AstroliftProviderPlugin[],
): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>();
  for (const p of plugins) {
    const manifest = (p.capabilitiesManifest ?? {}) as Record<string, unknown>;
    const rawDrivers = manifest.drivers;
    const set = new Set<string>();
    if (Array.isArray(rawDrivers)) {
      for (const r of rawDrivers) if (typeof r === "string") set.add(r);
    } else if (rawDrivers && typeof rawDrivers === "object") {
      for (const [k, v] of Object.entries(rawDrivers)) {
        if (v) set.add(k);
      }
    }
    for (const cap of CAPABILITIES) {
      if (manifest[cap.key] || manifest[`${cap.key}_driver`]) set.add(cap.key);
    }
    out.set(p.slug, set);
  }
  return out;
}

// Probed capabilities come back as a free-form JSON blob on the
// AstroliftTenantCluster (`capabilities` field). The canonical shape is
// flat: `{ "cluster": true, "ingress": true, "prometheus_endpoint": "..." }`.
// We only care about booleans matching our column keys here.
function clusterConfirms(
  capabilities: unknown,
  key: string,
): boolean {
  if (!capabilities || typeof capabilities !== "object") return false;
  const v = (capabilities as Record<string, unknown>)[key];
  return Boolean(v);
}

interface CellState {
  state: "confirmed" | "declared" | "absent";
}

function capabilityCellState(
  cluster: AstroliftTenantCluster,
  manifestKeys: Set<string> | undefined,
  key: string,
): CellState {
  const declared = manifestKeys?.has(key) ?? false;
  const confirmed = clusterConfirms(cluster.capabilities, key);
  if (declared && confirmed) return { state: "confirmed" };
  if (declared) return { state: "declared" };
  return { state: "absent" };
}

function CapabilityCell({ state }: { state: CellState }) {
  if (state.state === "confirmed") {
    return (
      <CheckCircle2Icon
        className="text-emerald-600 mx-auto size-4"
        aria-label="capability confirmed by probe"
      />
    );
  }
  if (state.state === "declared") {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">
            <AlertTriangleIcon
              className="text-amber-500 mx-auto size-4"
              aria-label="declared in manifest, not confirmed by probe"
            />
          </span>
        </TooltipTrigger>
        <TooltipContent>
          Declared in manifest, not confirmed by probe
        </TooltipContent>
      </Tooltip>
    );
  }
  return (
    <MinusIcon
      className="text-muted-foreground mx-auto size-4"
      aria-label="not declared in manifest"
    />
  );
}

export function ConnectedClustersClient() {
  const fmt = useFormatters();

  const { data: clustersData, loading: clustersLoading } = useQuery<ClustersResp>(
    LIST_CLUSTERS,
    { fetchPolicy: "cache-and-network" },
  );
  const { data: pluginsData } = useQuery<PluginsResp>(LIST_PROVIDER_PLUGINS, {
    fetchPolicy: "cache-first",
  });

  const manifestByProvider = React.useMemo(
    () => buildManifestCapabilities(pluginsData?.astroliftProviderPlugins ?? []),
    [pluginsData],
  );

  const [refresh] = useMutation<{
    refreshClusterManagement: MutationResult<AstroliftTenantCluster>;
  }>(REFRESH_CLUSTER_MANAGEMENT, {
    refetchQueries: [{ query: LIST_CLUSTERS }],
    awaitRefetchQueries: true,
  });
  const [pendingId, setPendingId] = React.useState<string | null>(null);

  async function handleReprobe(c: AstroliftTenantCluster) {
    setPendingId(c.id);
    try {
      const { data } = await refresh({
        variables: { input: { clusterId: c.id, forcePreflight: false } },
      });
      if (data?.refreshClusterManagement.ok) {
        toast.success(`Re-probing ${c.slug}`);
      } else {
        toast.error(
          data?.refreshClusterManagement.errors?.[0]?.message ??
            "Failed to re-probe cluster",
        );
      }
    } finally {
      setPendingId(null);
    }
  }

  const clusters = clustersData?.astroliftClusters ?? [];

  return (
    <PageShell
      title="Connected clusters"
      description="Live capability matrix for every registered tenant cluster. A green check means the provider's manifest declares the capability and the cluster's most recent probe confirmed it; an amber triangle means the manifest declares it but the probe didn't see it (likely a missing add-on or RBAC bundle)."
    >
      {clustersLoading && clusters.length === 0 ? (
        <Card>
          <CardContent className="p-6">
            <Skeleton className="h-44 w-full" />
          </CardContent>
        </Card>
      ) : clusters.length === 0 ? (
        <Card>
          <CardContent className="p-6">
            <EmptyState
              icon={<ServerIcon className="size-5" />}
              title="No clusters registered"
              description="Register a tenant cluster to see its live capability matrix here. Each row mirrors the columns on the Driver reference page, but reflects the cluster's most recent probe — not the static manifest."
              actionHref="/clusters"
              actionLabel="Go to Clusters"
              learnMoreHref="/resources/drivers"
              learnMoreLabel="View driver reference"
            />
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <ServerIcon className="size-4" />
              Capability matrix · live probe
            </CardTitle>
            <CardDescription>
              One row per registered cluster. Green check: provider manifest
              declares the capability and the cluster&apos;s probe confirmed
              it. Amber triangle: declared in manifest, not confirmed by the
              most recent probe. Grey dash: provider doesn&apos;t declare it
              at all.{" "}
              <Link
                href="/resources/drivers"
                className="text-primary hover:underline"
              >
                See driver reference →
              </Link>
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[220px]">Cluster</TableHead>
                  <TableHead className="w-[120px]">Provider</TableHead>
                  <TableHead className="w-[140px]">Lifecycle</TableHead>
                  {CAPABILITIES.map((cap) => (
                    <TableHead key={cap.key} className="text-center">
                      {cap.label}
                    </TableHead>
                  ))}
                  <TableHead className="w-[160px]">Last probed</TableHead>
                  <TableHead className="w-[80px] text-right">Re-probe</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {clusters.map((c) => {
                  const manifestKeys = manifestByProvider.get(
                    c.providerPluginSlug,
                  );
                  const isPending = pendingId === c.id;
                  return (
                    <React.Fragment key={c.id}>
                      <TableRow>
                        <TableCell>
                          <div className="flex flex-col gap-0.5">
                            <Link
                              href={`/clusters/${c.slug}`}
                              className="text-sm font-medium hover:underline"
                            >
                              {c.slug}
                            </Link>
                            <span className="text-muted-foreground font-mono text-xs">
                              {c.region || "—"}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary" className="font-mono text-2xs">
                            {c.providerPluginSlug}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <LifecycleBadge lifecycle={c.lifecycle} />
                        </TableCell>
                        {CAPABILITIES.map((cap) => (
                          <TableCell key={cap.key} className="text-center">
                            <CapabilityCell
                              state={capabilityCellState(c, manifestKeys, cap.key)}
                            />
                          </TableCell>
                        ))}
                        <TableCell className="text-muted-foreground text-xs">
                          {c.capabilitiesProbedAt
                            ? fmt.formatDateTime(c.capabilitiesProbedAt)
                            : "never"}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            size="icon"
                            variant="ghost"
                            className="size-8"
                            onClick={() => handleReprobe(c)}
                            disabled={isPending || c.lifecycle === "managing"}
                            aria-label={`Re-probe ${c.slug}`}
                          >
                            {isPending ? (
                              <Loader2Icon className="size-4 animate-spin" />
                            ) : (
                              <RotateCwIcon className="size-4" />
                            )}
                          </Button>
                        </TableCell>
                      </TableRow>
                      {c.lastManagementError ? (
                        <TableRow className="border-t-0">
                          <TableCell
                            colSpan={CAPABILITIES.length + 5}
                            className="pt-0 pb-3"
                          >
                            <p className="text-amber-700 dark:text-amber-400 inline-flex items-start gap-1.5 text-xs">
                              <AlertTriangleIcon className="mt-0.5 size-3 shrink-0" />
                              <span className="font-mono">
                                {c.lastManagementError}
                              </span>
                            </p>
                          </TableCell>
                        </TableRow>
                      ) : null}
                    </React.Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </PageShell>
  );
}
