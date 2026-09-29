"use client";

import { CpuIcon, MemoryStickIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Panel } from "@/components/panel/Panel";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import type { ResourceGauge, useWorkloadResourceUsage } from "./use-workload-resource-usage";

export type ResourceUsageGaugesViewProps = ReturnType<typeof useWorkloadResourceUsage>;

interface ResourceUsageLabels {
  title: string;
  cpuTitle: string;
  memoryTitle: string;
  ofRequest: string;
  ofLimit: string;
  currentLabel: string;
  requestLabel: string;
  limitLabel: string;
  empty: string;
  sourcedAt: string;
}

export function ResourceUsageGaugesView({ usage, loading }: ResourceUsageGaugesViewProps) {
  const t = useTranslations("apps.workloadDetail");
  const labels: ResourceUsageLabels = {
    title: t("resourceUsage.title"),
    cpuTitle: t("resourceUsage.cpuTitle"),
    memoryTitle: t("resourceUsage.memoryTitle"),
    ofRequest: t("resourceUsage.ofRequest"),
    ofLimit: t("resourceUsage.ofLimit"),
    currentLabel: t("resourceUsage.currentLabel"),
    requestLabel: t("resourceUsage.requestLabel"),
    limitLabel: t("resourceUsage.limitLabel"),
    empty: t("resourceUsage.empty"),
    sourcedAt: t("resourceUsage.sourcedAt"),
  };

  if (loading && !usage) {
    return (
      <Panel
        title={labels.title}
        span={6}
        loading
        skeleton={
          <div className="grid gap-3 sm:grid-cols-2">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        }
      />
    );
  }

  if (!usage) {
    return (
      <Panel title={labels.title} span={6}>
        <p className="text-muted-foreground text-sm">{labels.empty}</p>
      </Panel>
    );
  }

  return (
    <Panel
      title={labels.title}
      span={6}
      actions={
        <span className="text-muted-foreground font-mono text-xs">
          {labels.sourcedAt}: {formatSourcedAt(usage.sourcedAt)}
        </span>
      }
    >
      <div className="grid min-w-0 gap-3 sm:grid-cols-2">
        <GaugeCard
          title={labels.cpuTitle}
          icon={CpuIcon}
          gauge={usage.cpu}
          formatValue={formatCores}
          labels={labels}
        />
        <GaugeCard
          title={labels.memoryTitle}
          icon={MemoryStickIcon}
          gauge={usage.memory}
          formatValue={formatBytes}
          labels={labels}
        />
      </div>
    </Panel>
  );
}

function GaugeCard({
  title,
  icon: Icon,
  gauge,
  formatValue,
  labels,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  gauge: ResourceGauge;
  formatValue: (value: number) => string;
  labels: ResourceUsageLabels;
}) {
  // Prefer the limit as the denominator (it's the kubelet-enforced
  // cap and the number an operator cares about for OOM/throttle risk);
  // fall back to request when no limit is set.
  const denominator = gauge.limit > 0 ? "limit" : "request";
  const percent = denominator === "limit" ? gauge.percentOfLimit : gauge.percentOfRequest;
  const clampedPercent = Math.max(0, Math.min(100, percent));
  // Anything > 100% is over-budget. Render the bar at 100 but tag the
  // text in red so an over-budget pod is unmistakable.
  const overBudget = percent > 100;

  const color = colorFor(percent);

  return (
    <div className="border-muted rounded-md border p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Icon className="size-4" />
          {title}
        </div>
        <Badge variant="outline" className="font-mono text-xs">
          {denominator === "limit" ? labels.ofLimit : labels.ofRequest}
        </Badge>
      </div>
      <div className="mb-2 flex items-baseline justify-between">
        <span className={cn("font-mono text-2xl tabular-nums", overBudget && "text-danger-fg")}>
          {percent.toFixed(1)}%
        </span>
        <span className="text-muted-foreground font-mono text-xs">
          {formatValue(gauge.current)} /{" "}
          {formatValue(denominator === "limit" ? gauge.limit : gauge.request)}
        </span>
      </div>
      <div className="bg-muted h-2 w-full overflow-hidden rounded-full">
        <div
          className={cn("h-full transition-all", color)}
          style={{ width: `${clampedPercent}%` }}
        />
      </div>
      <dl className="text-muted-foreground mt-3 grid grid-cols-3 gap-1 text-xs">
        <div>
          <dt className="tracking-wide uppercase">{labels.currentLabel}</dt>
          <dd className="font-mono">{formatValue(gauge.current)}</dd>
        </div>
        <div>
          <dt className="tracking-wide uppercase">{labels.requestLabel}</dt>
          <dd className="font-mono">{formatValue(gauge.request)}</dd>
        </div>
        <div>
          <dt className="tracking-wide uppercase">{labels.limitLabel}</dt>
          <dd className="font-mono">{formatValue(gauge.limit)}</dd>
        </div>
      </dl>
    </div>
  );
}

// Color band per issue spec: green <50%, amber 50-80%, red >80%. We
// also push red at >100% (over-budget) which is the same color band —
// the percent text itself goes red at >100 so the two channels agree.
function colorFor(percent: number): string {
  if (percent >= 80) return "bg-danger";
  if (percent >= 50) return "bg-warning";
  return "bg-success";
}

function formatCores(cores: number): string {
  if (cores === 0) return "0";
  if (cores >= 1) return `${cores.toFixed(2)} cores`;
  // < 1 core — show millicores so single-digit usage isn't ambiguous.
  return `${Math.round(cores * 1000)}m`;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0";
  const KIB = 1024;
  const MIB = KIB * 1024;
  const GIB = MIB * 1024;
  if (bytes >= GIB) return `${(bytes / GIB).toFixed(2)} GiB`;
  if (bytes >= MIB) return `${(bytes / MIB).toFixed(0)} MiB`;
  if (bytes >= KIB) return `${(bytes / KIB).toFixed(0)} KiB`;
  return `${bytes} B`;
}

function formatSourcedAt(iso: string): string {
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return "—";
  const delta = Math.max(0, Date.now() - ms);
  const seconds = Math.floor(delta / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ago`;
}
