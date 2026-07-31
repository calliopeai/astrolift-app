"use client";

import { useMutation } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckCircleIcon,
  LayersIcon,
  Loader2Icon,
  MoreHorizontalIcon,
  PlayIcon,
  PlusIcon,
  RefreshCcwIcon,
  SearchXIcon,
  Trash2Icon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  DataTable,
  DataTablePagination,
  DataTableToolbar,
  useCursorTable,
  type Column,
  type CursorPage,
  type EmptyStateSpec,
} from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { ViewToggle } from "@/components/ViewToggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  BRING_CLUSTER_INTO_MANAGEMENT,
  LIST_CLUSTERS,
  LIST_CLUSTERS_PAGE,
  REFRESH_CLUSTER_MANAGEMENT,
  UNREGISTER_TENANT_CLUSTER,
} from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { useViewToggle } from "@/hooks/use-view-toggle";
import {
  formatHeartbeatAge,
  heartbeatPresentation,
  type ClusterHeartbeatFields,
  type HeartbeatStatus,
} from "@/lib/cluster-heartbeat";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";

import { RegisterClusterDialog } from "./register-cluster-dialog";

// The committed codegen output lags the live backend, so the generated
// AstroliftTenantCluster doesn't yet carry the heartbeat fields the
// cluster queries now select. Intersect them in locally.
type ClusterRow = AstroliftTenantCluster & Partial<ClusterHeartbeatFields>;

interface ClustersPageResp {
  astroliftClustersPage: CursorPage<ClusterRow>;
}

// Active polling cadence while any row is in the "managing" state.
// 4 seconds keeps the UI responsive to the workflow (which typically
// completes in 10-30s) without hammering the apiserver. Drops back to
// the heartbeat cadence as soon as no visible row is managing.
const POLL_INTERVAL_MS = 4000;

// Steady-state poll cadence to keep heartbeat-derived live status pills
// fresh (#808). Matches the default agent heartbeat interval.
const HEARTBEAT_POLL_INTERVAL_MS = 30000;

// This surface walks `ListClustersPage`, but LIST_CLUSTERS still backs the
// cluster detail tabs, /ops, /providers, /administration/metrics and the
// fleet map. Both have to be refreshed after a lifecycle change or one of
// the two goes stale — the walk by operation name, since its variables
// carry the cursor and the search term and no literal variables object
// names the page the operator is actually looking at.
const REFETCH_LIST = [{ query: LIST_CLUSTERS }, "ListClustersPage"];

// Both views render the same two empty states, so the copy lives in one
// place: switching card ↔ list must not change what the operator is told.
const EMPTY: EmptyStateSpec = {
  icon: <LayersIcon className="size-5" />,
  title: "No clusters registered",
  description:
    "Register a tenant Kubernetes cluster to record its metadata, then bring it into management once its prerequisites are installed.",
  learnMoreHref: "/documentation/cluster-prerequisites",
  learnMoreLabel: "Cluster prerequisites",
};

const EMPTY_FILTERED = {
  title: "No matching clusters",
  description:
    "No cluster matches that search. The server matches the cluster name, slug, endpoint, region and provider — try another term, or clear the search to see the whole fleet.",
};

// The row's link is an ::after overlay stretched across the whole row, and
// it paints above any cell that isn't lifted out of its way — a tooltip
// trigger or a button underneath it never receives the pointer. Anything
// interactive in a later cell carries this.
const ABOVE_ROW_LINK = "relative z-10";

type Lifecycle = "registered" | "managing" | "managed" | "error";

function LifecycleBadge({ lifecycle, error }: { lifecycle: Lifecycle; error?: string }) {
  // The lifecycle badge is the single most informative cell in the row
  // — semantic color and icon convey state without forcing the operator
  // to read the slug. Tooltip carries the error message on the error
  // state so the operator can fix without leaving the list.
  const presentation: Record<
    Lifecycle,
    {
      label: string;
      variant: "default" | "secondary" | "outline" | "destructive";
      icon: React.ReactNode;
    }
  > = {
    registered: {
      label: "Registered",
      variant: "outline",
      icon: <LayersIcon className="size-3" />,
    },
    managing: {
      label: "Managing…",
      variant: "secondary",
      icon: <Loader2Icon className="size-3 animate-spin" />,
    },
    managed: {
      label: "Managed",
      variant: "default",
      icon: <CheckCircleIcon className="size-3" />,
    },
    error: {
      label: "Error",
      variant: "destructive",
      icon: <AlertTriangleIcon className="size-3" />,
    },
  };
  const p = presentation[lifecycle];
  const badge = (
    <Badge variant={p.variant} className="gap-1">
      {p.icon}
      {p.label}
    </Badge>
  );
  if (lifecycle === "error" && error) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span>{badge}</span>
        </TooltipTrigger>
        <TooltipContent className="max-w-sm text-xs whitespace-pre-wrap">{error}</TooltipContent>
      </Tooltip>
    );
  }
  return badge;
}

// Live keep-alive status pill (#808). Driven by the heartbeat-derived
// status the backend computes; the tooltip carries the last-seen cue
// so an operator sees "Offline · last seen 12m ago" without leaving the
// list. Renders nothing meaningful for a cluster with no agent yet —
// "No agent" is the honest state, distinct from "Offline".
function HeartbeatBadge({
  status,
  ageSeconds,
}: {
  status: HeartbeatStatus | undefined;
  ageSeconds: number | null | undefined;
}) {
  const s = status ?? "never_seen";
  const p = heartbeatPresentation(s);
  const age = formatHeartbeatAge(ageSeconds ?? null);
  const badge = (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs ${p.pill}`}
    >
      <StatusDot status={p.dot} />
      {p.label}
    </span>
  );
  const tip =
    s === "never_seen"
      ? "No keep-alive agent has reported yet"
      : age
        ? `Last heartbeat ${age}`
        : "No recent heartbeat";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span>{badge}</span>
      </TooltipTrigger>
      <TooltipContent className="text-xs">{tip}</TooltipContent>
    </Tooltip>
  );
}

export function ClustersClient() {
  const fmt = useFormatters();
  const [viewMode, setViewMode] = useViewToggle("astrolift_view_clusters", "card");
  const [open, setOpen] = React.useState(false);
  const [unregisterTarget, setUnregisterTarget] = React.useState<AstroliftTenantCluster | null>(
    null
  );

  // `astroliftClustersPage` takes `search`, `limit` and `after` only —
  // there is no sort argument, so no column declares a `sortKey` and the
  // headers stay plain labels rather than controls that could only
  // reorder the page in hand (server-side sort is tracked in #1239).
  //
  // The controller's default `cache-and-network` is load-bearing here:
  // a cache-first read would answer from the SSR-primed result fetched
  // before the org cookie was set and never re-fetch, showing an empty
  // fleet to an org that has clusters.
  const table = useCursorTable<ClusterRow>({
    query: LIST_CLUSTERS_PAGE,
    extract: (d) => (d as ClustersPageResp | undefined)?.astroliftClustersPage,
    searchVariable: "search",
    urlKey: "cluster",
    // Steady-state cadence: keeps the heartbeat-derived Live pills fresh
    // without an operator reload (#808).
    pollInterval: HEARTBEAT_POLL_INTERVAL_MS,
  });

  // While a management workflow is in flight, overlay a faster refetch so
  // the lifecycle badge tracks the transition (it typically completes in
  // 10-30s). The cadence is decided by the page on screen rather than by
  // the whole fleet now that the walk is server-side — the fast poll
  // exists to animate a transition the operator is watching.
  const anyManaging = table.rows.some((c) => c.lifecycle === "managing");
  const { refetch } = table;
  React.useEffect(() => {
    if (!anyManaging) return;
    const id = setInterval(refetch, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [anyManaging, refetch]);

  const [unregister, { loading: deleting }] = useMutation<{
    unregisterTenantCluster: MutationResult<{ id: string; deleted: boolean }>;
  }>(UNREGISTER_TENANT_CLUSTER, {
    refetchQueries: REFETCH_LIST,
    awaitRefetchQueries: true,
  });
  const [bring, { loading: bringing }] = useMutation<{
    bringClusterIntoManagement: MutationResult<AstroliftTenantCluster>;
  }>(BRING_CLUSTER_INTO_MANAGEMENT, {
    refetchQueries: REFETCH_LIST,
    awaitRefetchQueries: true,
  });
  const [refresh, { loading: refreshing }] = useMutation<{
    refreshClusterManagement: MutationResult<AstroliftTenantCluster>;
  }>(REFRESH_CLUSTER_MANAGEMENT, {
    refetchQueries: REFETCH_LIST,
    awaitRefetchQueries: true,
  });

  async function handleUnregister(c: AstroliftTenantCluster) {
    const { data } = await unregister({ variables: { input: { id: c.id } } });
    if (data?.unregisterTenantCluster.ok) {
      toast.success(`Unregistered ${c.slug}`);
    } else {
      throw new Error(data?.unregisterTenantCluster.errors?.[0]?.message ?? "Failed");
    }
  }

  async function handleBring(c: AstroliftTenantCluster) {
    const { data } = await bring({ variables: { input: { clusterId: c.id } } });
    if (data?.bringClusterIntoManagement.ok) {
      toast.success(`Bringing ${c.slug} into management — this can take up to a minute.`);
    } else {
      toast.error(data?.bringClusterIntoManagement.errors?.[0]?.message ?? "Failed");
    }
  }

  async function handleRefresh(c: AstroliftTenantCluster, forcePreflight = false) {
    const { data } = await refresh({
      variables: { input: { clusterId: c.id, forcePreflight } },
    });
    if (data?.refreshClusterManagement.ok) {
      toast.success(
        forcePreflight ? `Refreshing ${c.slug} (full preflight)` : `Refreshing ${c.slug}`
      );
    } else {
      toast.error(data?.refreshClusterManagement.errors?.[0]?.message ?? "Failed");
    }
  }

  // The register dialog refetches LIST_CLUSTERS, which is a different root
  // field from the page this surface walks, so a newly registered cluster
  // would not appear until a navigation. Refetch the walk when it closes.
  function handleRegisterOpenChange(next: boolean) {
    setOpen(next);
    if (!next) table.refetch();
  }

  // Action descriptors. Each lifecycle resolves to one primary action
  // (managing has none — workflow is in flight). Reused by both the
  // md:+ inline button and the <md dropdown so the operator gets the
  // same affordances regardless of viewport.
  interface PrimaryAction {
    label: string;
    icon: React.ReactNode;
    onSelect: () => void;
    disabled?: boolean;
    variant?: "default" | "outline";
  }

  function primaryAction(c: AstroliftTenantCluster): PrimaryAction | null {
    const lifecycle = c.lifecycle as Lifecycle;
    if (lifecycle === "managing") return null;
    if (lifecycle === "managed") {
      return {
        label: "Refresh setup",
        icon: <RefreshCcwIcon className="size-4" />,
        onSelect: () => handleRefresh(c, false),
        disabled: refreshing,
        variant: "outline",
      };
    }
    if (lifecycle === "error") {
      return {
        label: "Retry",
        icon: <PlayIcon className="size-4" />,
        onSelect: () => handleBring(c),
        disabled: bringing,
        variant: "outline",
      };
    }
    return {
      label: "Bring into management",
      icon: <PlayIcon className="size-4" />,
      onSelect: () => handleBring(c),
      disabled: bringing,
      variant: "default",
    };
  }

  // Compact mobile-only action menu — below md: the desktop button
  // row would wrap awkwardly. The dropdown stacks the lifecycle-
  // appropriate primary action above Unregister and reuses the same
  // handler closures so behavior is identical. The trigger is a single
  // 3-dot icon button (44px tap target via size="icon" + size-9) so it
  // doesn't compete with the lifecycle badge for row real estate.
  function renderRowMenu(c: AstroliftTenantCluster) {
    const lifecycle = c.lifecycle as Lifecycle;
    const action = primaryAction(c);
    const busy = bringing || refreshing || deleting;
    return (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-9"
            disabled={lifecycle === "managing" && !action}
          >
            <MoreHorizontalIcon className="size-4" />
            <span className="sr-only">Cluster actions</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {lifecycle === "managing" && (
            <DropdownMenuItem disabled>
              <Loader2Icon className="size-4 animate-spin" />
              Setup in progress…
            </DropdownMenuItem>
          )}
          {action && (
            <Can permission="cluster.manage">
              <DropdownMenuItem onSelect={action.onSelect} disabled={action.disabled || busy}>
                {action.icon}
                {action.label}
              </DropdownMenuItem>
            </Can>
          )}
          <Can permission="cluster.unregister">
            <DropdownMenuItem
              variant="destructive"
              onSelect={() => setUnregisterTarget(c)}
              disabled={busy}
            >
              <Trash2Icon className="size-4" />
              Unregister
            </DropdownMenuItem>
          </Can>
        </DropdownMenuContent>
      </DropdownMenu>
    );
  }

  function renderActionButton(c: AstroliftTenantCluster) {
    const lifecycle = c.lifecycle as Lifecycle;
    if (lifecycle === "managing") {
      return (
        <Button size="sm" variant="ghost" disabled>
          <Loader2Icon className="size-4 animate-spin" />
          Setup in progress…
        </Button>
      );
    }
    const action = primaryAction(c);
    if (!action) return null;
    return (
      <Can permission="cluster.manage">
        <Button
          size="sm"
          variant={action.variant === "outline" ? "outline" : undefined}
          onClick={action.onSelect}
          disabled={action.disabled}
        >
          {action.icon}
          {action.label}
        </Button>
      </Can>
    );
  }

  const columns: Column<ClusterRow>[] = [
    {
      id: "cluster",
      header: "Cluster",
      // The active dot folds into this cell rather than sitting in a
      // column of its own: the first column carries the row link, and a
      // link whose only content is a coloured dot has no accessible name.
      cell: (c) => (
        <span className="flex items-start gap-2">
          <StatusDot status={c.isActive ? "ok" : "muted"} className="mt-1.5 shrink-0" />
          <span className="block">
            <span className="block font-medium">{c.name}</span>
            <span className="text-muted-foreground block font-mono text-xs">{c.slug}</span>
          </span>
        </span>
      ),
    },
    {
      id: "lifecycle",
      header: "Lifecycle",
      cell: (c) => (
        <span className={cn(ABOVE_ROW_LINK, "inline-flex")}>
          <LifecycleBadge
            lifecycle={(c.lifecycle as Lifecycle) ?? "registered"}
            error={c.lastManagementError ?? undefined}
          />
        </span>
      ),
    },
    {
      id: "provider",
      header: "Provider",
      cell: (c) => <Badge variant="outline">{c.providerPluginSlug}</Badge>,
    },
    {
      id: "region",
      header: "Region",
      cellClassName: "font-mono text-xs",
      cell: (c) => c.region || "—",
    },
    {
      id: "ingress",
      header: "Ingress",
      cellClassName: "font-mono text-xs",
      cell: (c) => c.ingressClass,
    },
    {
      id: "live",
      header: "Live",
      cell: (c) => (
        <span className={cn(ABOVE_ROW_LINK, "inline-flex")}>
          <HeartbeatBadge status={c.heartbeatStatus} ageSeconds={c.heartbeatAgeSeconds} />
        </span>
      ),
    },
    {
      id: "lastProbe",
      header: "Last probe",
      cellClassName: "text-muted-foreground text-sm",
      cell: (c) => (c.capabilitiesProbedAt ? fmt.formatDateTime(c.capabilitiesProbedAt) : "never"),
    },
    {
      id: "actions",
      header: "Actions",
      align: "right",
      cell: (c) => (
        <div className={cn(ABOVE_ROW_LINK, "flex items-center justify-end")}>
          <div className="hidden items-center justify-end gap-2 md:flex">
            {renderActionButton(c)}
            <Can permission="cluster.unregister">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setUnregisterTarget(c)}
                disabled={deleting}
              >
                <Trash2Icon className="size-4" />
                <span className="sr-only">Unregister</span>
              </Button>
            </Can>
          </div>
          <div className="flex items-center justify-end md:hidden">{renderRowMenu(c)}</div>
        </div>
      ),
    },
  ];

  // The card grid is a real view, not decoration, so it reads its rows
  // from the same controller the table does: search, page size and the
  // cursor walk apply identically in both modes. DataTable owns the four
  // states for the list view; the grid renders them itself, with the same
  // copy, so switching modes never changes what the operator is told.
  const cardBody = (() => {
    switch (table.state) {
      case "loading":
        // Skeleton cards, not a floating skeleton block: the placeholders
        // stand in the grid the cards will occupy, so nothing reflows when
        // the rows land.
        return (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Card key={`skeleton-${i}`}>
                <CardContent className="flex flex-col gap-3 p-5">
                  <Skeleton className="h-5 w-40" />
                  <Skeleton className="h-3 w-24" />
                  <div className="flex flex-wrap gap-2">
                    <Skeleton className="h-5 w-24 rounded-full" />
                    <Skeleton className="h-5 w-16 rounded-full" />
                    <Skeleton className="h-5 w-20 rounded-full" />
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        );

      case "error":
        return (
          <div className="flex flex-col items-center gap-3 rounded-md border py-10 text-center">
            <AlertTriangleIcon className="text-danger size-5" />
            <div>
              <p className="font-medium">Could not load clusters</p>
              <p className="text-muted-foreground mt-1 max-w-md text-sm">
                {table.error?.message ?? "The request failed."}
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={table.retry}>
              Retry
            </Button>
          </div>
        );

      case "emptyFiltered":
        return (
          <div className="flex flex-col items-center gap-3 rounded-md border py-10 text-center">
            <SearchXIcon className="text-muted-foreground size-5" />
            <div>
              <p className="font-medium">{EMPTY_FILTERED.title}</p>
              <p className="text-muted-foreground mt-1 max-w-md text-sm">
                {EMPTY_FILTERED.description}
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={table.clearFilters}>
              Clear search
            </Button>
          </div>
        );

      case "empty":
        return <EmptyState {...EMPTY} />;

      case "ready":
        return (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {table.rows.map((c) => (
              <a key={c.id} href={`/clusters/${c.slug}`} className="block">
                <Card className="hover:bg-accent/30 transition-colors">
                  <CardContent className="flex flex-col gap-3 p-5">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate font-semibold">{c.name}</div>
                        <div className="text-muted-foreground font-mono text-xs">{c.slug}</div>
                      </div>
                      <StatusDot status={c.isActive ? "ok" : "muted"} />
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <HeartbeatBadge
                        status={c.heartbeatStatus}
                        ageSeconds={c.heartbeatAgeSeconds}
                      />
                      <Badge variant="outline">{c.providerPluginSlug}</Badge>
                      <Badge variant="secondary">{c.region || "—"}</Badge>
                      <Badge variant="outline">{c.ingressClass}</Badge>
                    </div>
                  </CardContent>
                </Card>
              </a>
            ))}
          </div>
        );
    }
  })();

  return (
    <PageShell
      title="Clusters"
      description="Tenant Kubernetes clusters registered with the platform. Register a cluster to record its metadata, then click Bring into management when its prereqs (cert-manager, ingress controller) are installed."
      actions={
        <div className="flex items-center gap-2">
          <ViewToggle mode={viewMode} onChange={setViewMode} />
          <Can permission="cluster.register">
            <Button onClick={() => setOpen(true)}>
              <PlusIcon className="size-4" />
              Register cluster
            </Button>
          </Can>
        </div>
      }
    >
      {viewMode === "card" ? (
        <div className="flex flex-col gap-3">
          <DataTableToolbar controller={table} searchPlaceholder="Search clusters..." />
          {/* Rows persist across a refetch rather than blanking, so fade
              them while they answer the previous question — the same cue
              DataTable gives the list view. */}
          <div
            className={cn("transition-opacity", table.isStale && "opacity-60")}
            aria-busy={table.isStale || undefined}
          >
            {cardBody}
          </div>
          <DataTablePagination controller={table} />
        </div>
      ) : (
        <DataTable
          label="Clusters"
          controller={table}
          columns={columns}
          getRowId={(c) => c.id}
          rowHref={(c) => `/clusters/${c.slug}`}
          searchPlaceholder="Search clusters..."
          empty={EMPTY}
          emptyFiltered={EMPTY_FILTERED}
        />
      )}

      <RegisterClusterDialog open={open} onOpenChange={handleRegisterOpenChange} />

      <ConfirmDialog
        open={unregisterTarget !== null}
        onOpenChange={(next) => {
          if (!next) setUnregisterTarget(null);
        }}
        title={
          unregisterTarget ? `Unregister cluster ${unregisterTarget.slug}?` : "Unregister cluster?"
        }
        description="Refused if any active app still targets this cluster. The cluster's kubeconfig and probed capabilities are removed from the control plane."
        confirmLabel="Unregister"
        destructive
        onConfirm={async () => {
          if (unregisterTarget) await handleUnregister(unregisterTarget);
        }}
      />
    </PageShell>
  );
}
