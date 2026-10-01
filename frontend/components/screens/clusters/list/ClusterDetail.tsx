"use client";

import {
  Activity,
  AlertTriangleIcon,
  CheckCircleIcon,
  GlobeIcon,
  HardDriveIcon,
  LayersIcon,
  Loader2Icon,
  MoreHorizontalIcon,
  NetworkIcon,
  SettingsIcon,
  ShieldCheckIcon,
  Trash2Icon,
  TrendingUpIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useFormatters } from "@/lib/i18n/formatters";

import { ClusterHeader } from "./ClusterHeader";
import { lifecycleAction } from "./ClustersList";
import { type Lifecycle, providerLabel } from "./clusters-list";
import { useUnregisterReview } from "./use-unregister-review";
import type { useClusterDetail } from "./use-cluster-detail";

export type ClusterDetailProps = ReturnType<typeof useClusterDetail> & {
  /** The live stats row, rendered by the caller so its polling queries stay out of this view. */
  renderLiveStats: (clusterId: string) => React.ReactNode;
};

// ─── Static maps ─────────────────────────────────────────────────────
const LIFECYCLE_CONFIG: Record<
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

// ─── Capability config ────────────────────────────────────────────────
type CapKey =
  | "cert_manager"
  | "ingress"
  | "external_dns"
  | "storage_classes"
  | "metrics_server"
  | "prometheus";

const CAPABILITY_META: Record<CapKey, { label: string; icon: React.ReactNode }> = {
  cert_manager: {
    label: "cert-manager",
    icon: <ShieldCheckIcon className="size-4" />,
  },
  ingress: {
    label: "Ingress",
    icon: <NetworkIcon className="size-4" />,
  },
  external_dns: {
    label: "external-dns",
    icon: <GlobeIcon className="size-4" />,
  },
  storage_classes: {
    label: "Storage classes",
    icon: <HardDriveIcon className="size-4" />,
  },
  metrics_server: {
    label: "Metrics server",
    icon: <Activity className="size-4" />,
  },
  prometheus: {
    label: "Prometheus",
    icon: <TrendingUpIcon className="size-4" />,
  },
};

function isCapInstalled(key: CapKey, value: unknown): boolean {
  if (key === "storage_classes") return Array.isArray(value) && value.length > 0;
  if (value && typeof value === "object") {
    return !!(value as Record<string, unknown>).installed;
  }
  return !!value;
}

/**
 * The cluster overview tab, under the shared cluster header. Pure view; the
 * data half is useClusterDetail.
 */
export function ClusterDetail(props: ClusterDetailProps) {
  const { slug, cluster, loading, renderLiveStats, deleting, onUnregister } = props;
  const fmt = useFormatters();
  const t = useTranslations("clusters.unregister");
  const unregister = useUnregisterReview(cluster ? [cluster] : [], slug);
  const lifecycle = (cluster?.lifecycle as Lifecycle | undefined) ?? "registered";

  if (!cluster) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <ClusterHeader slug={slug} cluster={null} loading={loading} active="overview" />
        {loading ? (
          <>
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-48 w-full" />
          </>
        ) : (
          <EmptyState
            icon={<AlertTriangleIcon className="size-5" />}
            title={`No cluster with slug ${slug}`}
            description="The cluster doesn't exist or you don't have permission to view it."
            actionHref="/clusters"
            actionLabel="Back to clusters"
          />
        )}
      </div>
    );
  }

  const provider = providerLabel(cluster.providerPluginSlug);
  const lc = LIFECYCLE_CONFIG[lifecycle];
  const caps = (cluster.capabilities ?? {}) as Record<string, unknown>;
  const hasCaps = Object.keys(caps).length > 0;
  const action = lifecycleAction(cluster, props);

  const primaryAction =
    lifecycle === "managing" ? (
      <Button size="sm" variant="ghost" disabled>
        <Loader2Icon className="size-4 animate-spin" />
        Setup in progress…
      </Button>
    ) : action ? (
      <Can permission="cluster.manage">
        <Button
          size="sm"
          variant={action.variant === "outline" ? "outline" : undefined}
          onClick={action.run}
          disabled={action.disabled}
        >
          {action.icon}
          {action.label}
        </Button>
      </Can>
    ) : null;

  const menu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="icon" variant="ghost" className="size-8" aria-label="Cluster actions">
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-40">
        <DropdownMenuItem asChild>
          <Link href={`/clusters/${cluster.slug}/settings`}>
            <SettingsIcon className="size-4" />
            Settings
          </Link>
        </DropdownMenuItem>
        <Can permission="cluster.unregister">
          <DropdownMenuItem
            variant="destructive"
            onSelect={() => unregister.open(cluster)}
            disabled={deleting}
          >
            <Trash2Icon className="size-4" />
            {t("action")}
          </DropdownMenuItem>
        </Can>
      </DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <ClusterHeader
        slug={slug}
        cluster={cluster}
        active="overview"
        primaryAction={primaryAction}
        menu={menu}
      />

      {/* ── Error card: a failure's reason leads the page (spec 44 §5.2) ── */}
      {lifecycle === "error" && cluster.lastManagementError && (
        <Card className="border-l-destructive border-destructive/40 bg-destructive/5 !rounded-none border-l-4 shadow-md">
          <CardHeader>
            <CardTitle className="text-destructive flex items-center gap-2 text-base">
              <AlertTriangleIcon className="size-4" />
              Last management failure
            </CardTitle>
            <CardDescription>
              Fix the underlying issue and click Retry from Settings to re-run.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="text-destructive font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
              {cluster.lastManagementError}
            </pre>
          </CardContent>
        </Card>
      )}

      {/* ── Management status card ──────────────────────────────────── */}
      <Card className="border-l-muted-foreground !rounded-none border-l-4 shadow-md">
        <CardHeader className="pb-3">
          <div className="flex min-w-0 items-start justify-between gap-4">
            <div className="min-w-0">
              <CardTitle className="text-base">Management</CardTitle>
              <CardDescription className="mt-0.5">
                {lifecycle === "managed" && cluster.managedAt
                  ? `Active since ${fmt.formatDateTime(cluster.managedAt)}`
                  : cluster.capabilitiesProbedAt
                    ? `Capabilities probed ${fmt.formatDateTime(cluster.capabilitiesProbedAt)}`
                    : "Not yet brought into management"}
              </CardDescription>
            </div>
            <Badge variant={lc.variant} className="shrink-0 gap-1">
              {lc.icon}
              {lc.label}
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
            <div className="min-w-0">
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">Provider</dt>
              <dd className="mt-0.5 font-medium">{provider}</dd>
            </div>
            <div className="min-w-0">
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">Region</dt>
              <dd className="mt-0.5 font-mono [overflow-wrap:anywhere]">{cluster.region || "—"}</dd>
            </div>
            <div className="min-w-0">
              <dt className="text-muted-foreground text-xs tracking-wide uppercase">Auth</dt>
              <dd className="mt-0.5 font-mono [overflow-wrap:anywhere]">
                {cluster.authMethod || "—"}
              </dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      {/* ── Live stats row ──────────────────────────────────────────── */}
      {renderLiveStats(cluster.id)}

      {/* ── Capabilities grid ───────────────────────────────────────── */}
      {hasCaps && (
        <Card className="!rounded-none shadow-md">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Capabilities</CardTitle>
            <CardDescription>
              Detected during the last capability probe. Refresh cluster management to update.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {(Object.keys(CAPABILITY_META) as CapKey[]).map((key) => {
                const meta = CAPABILITY_META[key];
                const installed = isCapInstalled(key, caps[key]);
                return (
                  <div
                    key={key}
                    className={`flex items-center gap-3 border p-3 ${
                      installed ? "border-border bg-muted/40" : "border-border bg-muted/30"
                    }`}
                  >
                    <span
                      className={`shrink-0 p-1.5 ${
                        installed ? "bg-muted text-foreground" : "bg-muted text-muted-foreground"
                      }`}
                    >
                      {meta.icon}
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-sm leading-snug font-medium">{meta.label}</p>
                      <p className="text-muted-foreground mt-0.5 text-xs">
                        {installed ? "Installed" : "Not detected"}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* ── Action required ─────────────────────────────────────────── */}
      {lifecycle !== "managed" && lifecycle !== "managing" && (
        <div className="border-border bg-muted/40 border p-4 text-sm">
          <p className="font-medium">Action required</p>
          <p className="text-muted-foreground mt-1">
            This cluster isn&apos;t managed yet.{" "}
            <Link
              href={`/clusters/${slug}/settings`}
              className="text-primary underline-offset-4 hover:underline"
            >
              Go to Settings
            </Link>{" "}
            to bring it into management.
          </p>
        </div>
      )}

      <ConfirmDialog
        key={unregister.key}
        open={unregister.target !== null}
        onOpenChange={unregister.onOpenChange}
        title={t("title", { slug: unregister.target?.slug ?? cluster.slug })}
        description={t("description")}
        confirmLabel={t("action")}
        destructive
        onConfirm={() => unregister.confirm(onUnregister)}
      />
    </div>
  );
}
