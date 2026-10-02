"use client";

import { AlertTriangleIcon, ChartSplineIcon, ExternalLinkIcon } from "lucide-react";
import Link from "next/link";
import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";

import { Panel, type PanelSpan } from "@/components/panel/Panel";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

import type { ObservabilitySummary, SparkPoint } from "./use-observability-summary";

export interface ObservabilitySectionViewProps extends ObservabilitySummary {
  appSlug: string;
  span?: PanelSpan;
}

/**
 * Compact observability summary: deploys-per-day and failure-rate
 * sparklines computed from the recent deploy stream, plus a count of
 * unresolved alert events with a link to the alerts page. Deep-links to
 * `/administration/metrics?app=<slug>` and
 * `/alerts/events?view=firing&app=<slug>` for the full surface.
 */
export function ObservabilitySectionView({
  appSlug,
  days,
  error,
  onRetry,
  deploysLoading,
  deploysSeries,
  errorSeries,
  totalDeploys,
  failedCount,
  unresolvedCount,
  criticalCount,
  alertsLoading,
  span = 6,
}: ObservabilitySectionViewProps) {
  return (
    <Panel
      title="Observability"
      icon={<ChartSplineIcon className="size-4" />}
      description={`Last ${days} days · deploy throughput, failure rate, unresolved alerts.`}
      span={span}
      error={error}
      onRetry={onRetry}
      actions={
        <>
          <Link
            href={`/administration/metrics?app=${appSlug}`}
            className="text-primary inline-flex items-center gap-1 text-xs hover:underline"
          >
            Metrics
            <ExternalLinkIcon className="size-3" />
          </Link>
          <Link
            href={`/alerts/events?view=firing&app=${encodeURIComponent(appSlug)}`}
            className="text-primary inline-flex items-center gap-1 text-xs hover:underline"
          >
            Alerts
            <ExternalLinkIcon className="size-3" />
          </Link>
        </>
      }
    >
      {deploysLoading ? (
        <div className="grid min-w-0 gap-3 sm:grid-cols-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <div className="grid min-w-0 gap-3 sm:grid-cols-3">
          <SparkCard
            label="Deploys / day"
            value={totalDeploys}
            sublabel={`across ${days} days`}
            color="var(--primary)"
            data={deploysSeries}
            empty={totalDeploys === 0 ? "No deploys" : undefined}
          />
          <SparkCard
            label="Failures"
            value={failedCount}
            sublabel={
              totalDeploys > 0
                ? `${formatPct(failedCount / totalDeploys)} of rollouts`
                : "no rollouts yet"
            }
            color="var(--danger)"
            data={errorSeries}
            empty={failedCount === 0 ? "No failures" : undefined}
          />
          <AlertSummaryCard
            unresolved={unresolvedCount}
            critical={criticalCount}
            appSlug={appSlug}
            loading={alertsLoading}
          />
        </div>
      )}
    </Panel>
  );
}

function SparkCard({
  label,
  value,
  sublabel,
  color,
  data,
  empty,
}: {
  label: string;
  value: number;
  sublabel: string;
  color: string;
  data: SparkPoint[];
  empty?: string;
}) {
  return (
    <div className="flex min-w-0 flex-col rounded-md border p-3">
      <p className="text-muted-foreground text-2xs font-medium tracking-wide uppercase">{label}</p>
      <p className="mt-0.5 font-mono text-2xl font-semibold">{value}</p>
      <p className="text-muted-foreground text-2xs">{sublabel}</p>
      <div className="mt-2 h-12">
        {empty ? (
          <div className="text-muted-foreground text-2xs flex h-full items-center justify-center italic">
            {empty}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
              <YAxis hide domain={[0, "auto"]} />
              <Tooltip
                cursor={false}
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const p = payload[0].payload as SparkPoint;
                  return (
                    <div className="bg-background text-2xs rounded border px-2 py-1 shadow-sm">
                      <p className="font-mono">{p.value}</p>
                      <p className="text-muted-foreground">
                        {new Date(p.ts).toLocaleDateString(undefined, {
                          month: "short",
                          day: "numeric",
                        })}
                      </p>
                    </div>
                  );
                }}
              />
              <Area
                type="monotone"
                dataKey="value"
                stroke={color}
                fill={color}
                fillOpacity={0.18}
                strokeWidth={1.5}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

function AlertSummaryCard({
  unresolved,
  critical,
  appSlug,
  loading,
}: {
  unresolved: number;
  critical: number;
  appSlug: string;
  loading: boolean;
}) {
  return (
    <Link
      href={`/alerts/events?view=firing&app=${encodeURIComponent(appSlug)}`}
      className="hover:bg-muted/40 group flex min-w-0 flex-col rounded-md border p-3 transition-colors"
    >
      <p className="text-muted-foreground text-2xs flex items-center gap-1 font-medium tracking-wide uppercase">
        <AlertTriangleIcon className="size-3" />
        Unresolved alerts
      </p>
      <p className="mt-0.5 font-mono text-2xl font-semibold">{loading ? "…" : unresolved}</p>
      <p className="text-muted-foreground text-2xs">
        {critical > 0 ? (
          <Badge variant="outline" className="border-danger-border text-danger-fg">
            {critical} critical
          </Badge>
        ) : unresolved === 0 ? (
          "All clear"
        ) : (
          `${unresolved} pending`
        )}
      </p>
      <div className="mt-auto flex items-center justify-end pt-3">
        <span className="text-muted-foreground text-2xs group-hover:text-primary inline-flex items-center gap-1">
          Open
          <ExternalLinkIcon className="size-3" />
        </span>
      </div>
    </Link>
  );
}

function formatPct(v: number): string {
  return `${Math.round(v * 100)}%`;
}
