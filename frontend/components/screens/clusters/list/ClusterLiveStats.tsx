"use client";

import { Activity, CheckCircle2Icon, DatabaseIcon, LayersIcon, ServerIcon } from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import type { useClusterLiveStats } from "./use-cluster-live-stats";

// ─── Live stats row ───────────────────────────────────────────────────
// Apps bound (from cluster query) + Prometheus instant metrics.
// Prometheus tiles degrade gracefully to "—" when unavailable.

export type ClusterLiveStatsProps = ReturnType<typeof useClusterLiveStats>;

function pct(v: number | null): string {
  if (v === null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function utilizationTone(v: number | null): string {
  if (v === null) return "text-foreground";
  if (v < 0.7) return "text-success-fg";
  if (v < 0.9) return "text-warning-fg";
  return "text-destructive";
}

function healthTone(v: number | null): string {
  if (v === null) return "text-foreground";
  if (v >= 0.9) return "text-success-fg";
  if (v >= 0.7) return "text-warning-fg";
  return "text-destructive";
}

interface StatTileProps {
  label: string;
  value: React.ReactNode;
  icon: React.ReactNode;
  valueClass?: string;
  loading?: boolean;
}

function StatTile({ label, value, icon, valueClass = "text-foreground", loading }: StatTileProps) {
  return (
    <Card className="!rounded-none shadow-md">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 px-4 pt-4 pb-2">
        <span className="text-muted-foreground text-xs tracking-wide uppercase">{label}</span>
        <span className="text-muted-foreground">{icon}</span>
      </CardHeader>
      <CardContent className="px-4 pb-4">
        {loading ? (
          <Skeleton className="h-7 w-16" />
        ) : (
          <p className={`text-2xl font-semibold tabular-nums ${valueClass}`}>{value}</p>
        )}
      </CardContent>
    </Card>
  );
}

/** The cluster overview's live stats row. Pure view; the data half is useClusterLiveStats. */
export function ClusterLiveStats({
  appCount,
  appLoading,
  metrics: m,
  metricsLoading,
}: ClusterLiveStatsProps) {
  const t = useTranslations("clusters.detail.stats");
  const hasLiveData = m?.available;

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      <StatTile
        label={t("appsBound")}
        value={appCount ?? "—"}
        icon={<LayersIcon className="size-4" />}
        loading={appLoading}
      />
      <StatTile
        label={t("nodes")}
        value={hasLiveData && m.nodeCount !== null ? m.nodeCount : "—"}
        icon={<ServerIcon className="size-4" />}
        loading={metricsLoading}
      />
      <StatTile
        label={t("podsRunning")}
        value={hasLiveData ? pct(m.podRunningRatio) : "—"}
        icon={<CheckCircle2Icon className="size-4" />}
        valueClass={hasLiveData ? healthTone(m.podRunningRatio) : "text-muted-foreground"}
        loading={metricsLoading}
      />
      <StatTile
        label={t("cpu")}
        value={hasLiveData ? pct(m.cpuUtilization) : "—"}
        icon={<Activity className="size-4" />}
        valueClass={hasLiveData ? utilizationTone(m.cpuUtilization) : "text-muted-foreground"}
        loading={metricsLoading}
      />
      <StatTile
        label={t("memory")}
        value={hasLiveData ? pct(m.memoryUtilization) : "—"}
        icon={<DatabaseIcon className="size-4" />}
        valueClass={hasLiveData ? utilizationTone(m.memoryUtilization) : "text-muted-foreground"}
        loading={metricsLoading}
      />
    </div>
  );
}
