"use client";

import {
  AlertTriangleIcon,
  CheckCircleIcon,
  LayersIcon,
  Loader2Icon,
  PlayIcon,
  PlusIcon,
  RefreshCcwIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column, EmptyStateSpec } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import {
  formatHeartbeatAge,
  heartbeatPresentation,
  type HeartbeatStatus,
} from "@/lib/cluster-heartbeat";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";

import { clusterCrumbs, LIFECYCLE_LABEL, type Lifecycle, providerLabel } from "./clusters-list";
import type { ClusterActions } from "./use-cluster-actions";
import type { ClusterRow } from "./use-clusters-list";

export type ClustersListProps = ClusterActions & {
  list: ListStateController;
  /** The page on screen, already filtered, sorted and sliced. */
  rows: ClusterRow[];
  /** Clusters matching the view, filters and search, across all pages. */
  totalCount: number;
  loading: boolean;
  stale: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  /** The register flow: a page, since it asks for more than three fields (spec 44 §5.4). */
  registerHref: string;
};

const EMPTY: EmptyStateSpec = {
  icon: <LayersIcon className="size-5" />,
  title: "No clusters registered",
  description:
    "Register a tenant Kubernetes cluster to record its metadata, then bring it into management once its prerequisites are installed.",
  learnMoreHref: "/documentation/cluster-prerequisites",
  learnMoreLabel: "Cluster prerequisites",
};

// The row's link is an ::after overlay stretched across the whole row, and
// it paints above any cell that isn't lifted out of its way — a tooltip
// trigger underneath it never receives the pointer.
const ABOVE_ROW_LINK = "relative z-10";

const LIFECYCLE_BADGE: Record<
  Lifecycle,
  { variant: "default" | "secondary" | "outline" | "destructive"; icon: React.ReactNode }
> = {
  registered: { variant: "outline", icon: <LayersIcon className="size-3" /> },
  managing: { variant: "secondary", icon: <Loader2Icon className="size-3 animate-spin" /> },
  managed: { variant: "default", icon: <CheckCircleIcon className="size-3" /> },
  error: { variant: "destructive", icon: <AlertTriangleIcon className="size-3" /> },
};

/** The lifecycle badge; on error its tooltip carries the failure, so it can be fixed from the list. */
export function LifecycleBadge({ lifecycle, error }: { lifecycle: Lifecycle; error?: string }) {
  const p = LIFECYCLE_BADGE[lifecycle] ?? LIFECYCLE_BADGE.registered;
  const badge = (
    <Badge variant={p.variant} className="gap-1">
      {p.icon}
      {LIFECYCLE_LABEL[lifecycle] ?? lifecycle}
    </Badge>
  );
  if (lifecycle === "error" && error) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span>{badge}</span>
        </TooltipTrigger>
        <TooltipContent className="max-w-sm text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
          {error}
        </TooltipContent>
      </Tooltip>
    );
  }
  return badge;
}

// Live keep-alive status pill (#808). "No agent" is the honest state for a
// cluster whose agent never reported, distinct from "Offline".
export function HeartbeatBadge({
  status,
  ageSeconds,
}: {
  status: HeartbeatStatus | undefined;
  ageSeconds: number | null | undefined;
}) {
  const s = status ?? "never_seen";
  const p = heartbeatPresentation(s);
  const age = formatHeartbeatAge(ageSeconds ?? null);
  const tip =
    s === "never_seen"
      ? "No keep-alive agent has reported yet"
      : age
        ? `Last heartbeat ${age}`
        : "No recent heartbeat";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs",
            p.pill
          )}
        >
          <StatusDot status={p.dot} />
          {p.label}
        </span>
      </TooltipTrigger>
      <TooltipContent className="font-mono text-xs">{tip}</TooltipContent>
    </Tooltip>
  );
}

interface LifecycleAction {
  label: string;
  icon: React.ReactNode;
  run: () => void;
  disabled: boolean;
  variant: "default" | "outline";
}

/** The one lifecycle action a cluster offers; none while a workflow is in flight. */
export function lifecycleAction(
  c: AstroliftTenantCluster,
  { bringing, refreshing, onBring, onRefresh }: ClusterActions
): LifecycleAction | null {
  switch (c.lifecycle as Lifecycle) {
    case "managing":
      return null;
    case "managed":
      return {
        label: "Refresh setup",
        icon: <RefreshCcwIcon className="size-4" />,
        run: () => void onRefresh(c, false),
        disabled: refreshing,
        variant: "outline",
      };
    case "error":
      return {
        label: "Retry",
        icon: <PlayIcon className="size-4" />,
        run: () => void onBring(c),
        disabled: bringing,
        variant: "outline",
      };
    default:
      return {
        label: "Bring into management",
        icon: <PlayIcon className="size-4" />,
        run: () => void onBring(c),
        disabled: bringing,
        variant: "default",
      };
  }
}

function ClusterCard(c: ClusterRow) {
  return (
    <div className="bg-card hover:bg-accent/30 flex h-full min-w-0 flex-col gap-3 rounded-md border p-4 transition-colors">
      <div className="flex min-w-0 items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-semibold" title={c.name}>
            {c.name}
          </div>
          <div className="text-muted-foreground truncate font-mono text-xs" title={c.slug}>
            {c.slug}
          </div>
        </div>
        <StatusDot status={c.isActive ? "ok" : "muted"} className="mt-1.5" />
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
        <HeartbeatBadge status={c.heartbeatStatus} ageSeconds={c.heartbeatAgeSeconds} />
        <Badge variant="outline" className="max-w-full min-w-0 truncate font-mono">
          {c.providerPluginSlug}
        </Badge>
        <Badge variant="secondary" className="max-w-full min-w-0 truncate font-mono">
          {c.region || "—"}
        </Badge>
        <Badge variant="outline" className="max-w-full min-w-0 truncate font-mono">
          {c.ingressClass}
        </Badge>
      </div>
    </div>
  );
}

/**
 * Admin › Clusters (spec 44 §5.1): the fleet on the shared list, views All ·
 * Mine · Offline, provider and status filters, numbered pages, list or
 * cards. Pure view; the data half is useClustersList.
 */
export function ClustersList(props: ClustersListProps) {
  const {
    list,
    rows,
    totalCount,
    loading,
    stale,
    error,
    onRetry,
    registerHref,
    deleting,
    bringing,
    refreshing,
    onUnregister,
  } = props;
  const fmt = useFormatters();
  const [unregisterTarget, setUnregisterTarget] = React.useState<AstroliftTenantCluster | null>(
    null
  );
  const busy = bringing || refreshing || deleting;

  const columns: Column<ClusterRow>[] = [
    {
      id: "cluster",
      header: "Cluster",
      sortKey: "name",
      cellClassName: "max-w-80",
      // The active dot folds into this cell: the first column carries the
      // row link, and a link whose only content is a dot has no name.
      cell: (c) => (
        <span className="flex min-w-0 items-start gap-2">
          <StatusDot status={c.isActive ? "ok" : "muted"} className="mt-1.5" />
          <span className="block min-w-0">
            <span className="block truncate font-medium" title={c.name}>
              {c.name}
            </span>
            <span className="text-muted-foreground block truncate font-mono text-xs" title={c.slug}>
              {c.slug}
            </span>
          </span>
        </span>
      ),
    },
    {
      id: "status",
      header: "Status",
      sortKey: "status",
      cell: (c) => (
        <span className={cn(ABOVE_ROW_LINK, "inline-flex")}>
          <LifecycleBadge
            lifecycle={(c.lifecycle as Lifecycle) ?? "registered"}
            error={c.lastManagementError || undefined}
          />
        </span>
      ),
    },
    {
      id: "provider",
      header: "Provider",
      sortKey: "provider",
      cell: (c) => (
        <Badge variant="outline" className="font-mono" title={providerLabel(c.providerPluginSlug)}>
          {c.providerPluginSlug}
        </Badge>
      ),
    },
    {
      id: "region",
      header: "Region",
      sortKey: "region",
      cellClassName: "max-w-48",
      cell: (c) => (
        <span className="block min-w-0 truncate font-mono text-xs" title={c.region}>
          {c.region || "—"}
        </span>
      ),
    },
    {
      id: "ingress",
      header: "Ingress",
      cellClassName: "max-w-48",
      cell: (c) => (
        <span className="block min-w-0 truncate font-mono text-xs" title={c.ingressClass}>
          {c.ingressClass}
        </span>
      ),
    },
    {
      id: "live",
      header: "Live",
      sortKey: "live",
      cell: (c) => (
        <span className={cn(ABOVE_ROW_LINK, "inline-flex")}>
          <HeartbeatBadge status={c.heartbeatStatus} ageSeconds={c.heartbeatAgeSeconds} />
        </span>
      ),
    },
    {
      id: "lastProbe",
      header: "Last probe",
      sortKey: "lastProbe",
      cell: (c) => (
        <span className="text-muted-foreground font-mono text-xs">
          {c.capabilitiesProbedAt ? fmt.formatDateTime(c.capabilitiesProbedAt) : "never"}
        </span>
      ),
    },
  ];

  function rowActions(c: ClusterRow) {
    const action = lifecycleAction(c, props);
    return (
      <>
        {c.lifecycle === "managing" && (
          <DropdownMenuItem disabled>
            <Loader2Icon className="size-4 animate-spin" />
            Setup in progress…
          </DropdownMenuItem>
        )}
        {action && (
          <Can permission="cluster.manage">
            <DropdownMenuItem onSelect={action.run} disabled={action.disabled || busy}>
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
      </>
    );
  }

  return (
    <>
      <ListPage<ClusterRow>
        header={{
          crumbs: clusterCrumbs(),
          title: "Clusters",
          primaryAction: (
            <Can permission="cluster.register">
              <Button size="sm" asChild>
                <Link href={registerHref}>
                  <PlusIcon className="size-4" />
                  Register cluster
                </Link>
              </Button>
            </Can>
          ),
        }}
        list={list}
        label="Clusters"
        columns={columns}
        rows={rows}
        getRowId={(c) => c.id}
        rowHref={(c) => `/clusters/${c.slug}`}
        rowActions={rowActions}
        renderCard={ClusterCard}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        // No create action here: it would show to viewers without cluster.register.
        empty={EMPTY}
        totalCount={totalCount}
      />

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
          if (unregisterTarget) await onUnregister(unregisterTarget);
        }}
      />
    </>
  );
}
