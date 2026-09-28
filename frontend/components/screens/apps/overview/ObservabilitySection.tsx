"use client";

import { AlertTriangleIcon, ChartSplineIcon, ExternalLinkIcon } from "lucide-react";
import Link from "next/link";
import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";

import { Badge } from "@/components/ui/badge";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";

import type { ObservabilitySummary, SparkPoint } from "./use-observability-summary";

export interface ObservabilitySectionViewProps extends ObservabilitySummary {
  appSlug: string;
}

/**
 * Compact observability summary: deploys-per-day and failure-rate
 * sparklines computed from the recent deploy stream, plus a count of
 * unresolved alert events with a link to the alerts page. Deep-links to
 * `/metrics/<app>` and `/alerts?app=<slug>` for the full surface.
 */
export function ObservabilitySectionView({
  appSlug,
  days,
  deploysLoading,
  deploysSeries,
  errorSeries,
  totalDeploys,
  failedCount,
  unresolvedCount,
  criticalCount,
  alertsLoading,
}: ObservabilitySectionViewProps) {
  return (
    <Section
      // Rendered inside the page-level "Insights" Section — nest the outline.
      headingLevel="h3"
      title={
        <span className="flex items-center gap-2">
          <ChartSplineIcon className="text-muted-foreground size-4" />
          Observability
        </span>
      }
      description={`Last ${days} days · deploy throughput, failure rate, unresolved alerts.`}
      action={
        <>
          <Link
            href={`/administration/metrics?app=${appSlug}`}
            className="inline-flex items-center gap-1 text-xs text-[var(--brand-primary)] hover:underline"
          >
            Open metrics
            <ExternalLinkIcon className="size-3" />
          </Link>
          <Link
            href={`/alerts?app=${appSlug}`}
            className="inline-flex items-center gap-1 text-xs text-[var(--brand-primary)] hover:underline"
          >
            Open alerts
            <ExternalLinkIcon className="size-3" />
          </Link>
        </>
      }
    >
      {deploysLoading && totalDeploys === 0 ? (
        <div className="grid gap-3 sm:grid-cols-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-3">
          <SparkCard
            label="Deploys / day"
            value={totalDeploys}
            sublabel={`across ${days} days`}
            color="rgb(8 212 184)"
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
            color="rgb(239 68 68)"
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
    </Section>
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
    <div className="bg-card flex flex-col rounded-md border p-3">
      <p className="text-muted-foreground text-2xs font-medium tracking-wide uppercase">{label}</p>
      <p className="mt-0.5 text-2xl font-semibold">{value}</p>
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
      href={`/alerts?app=${appSlug}`}
      className="bg-card hover:bg-muted/40 group flex flex-col rounded-md border p-3 transition-colors"
    >
      <p className="text-muted-foreground text-2xs flex items-center gap-1 font-medium tracking-wide uppercase">
        <AlertTriangleIcon className="size-3" />
        Unresolved alerts
      </p>
      <p className="mt-0.5 text-2xl font-semibold">{loading ? "—" : unresolved}</p>
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
        <span className="text-muted-foreground text-2xs inline-flex items-center gap-1 group-hover:text-[var(--brand-primary)]">
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
