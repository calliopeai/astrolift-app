"use client";

import { useQuery } from "@apollo/client/react";
import { AlertTriangleIcon, ChartSplineIcon, ExternalLinkIcon } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";

import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_ALERT_EVENTS } from "@/graphql/operations/alerts.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

interface AlertEventsResp {
  astroliftAlertEvents: Array<{
    id: string;
    severity: string;
    firedAt: string;
    resolvedAt: string | null;
    acknowledgedAt: string | null;
    summary: string;
  }>;
}

interface SparkPoint {
  ts: string;
  value: number;
}

interface Props {
  appSlug: string;
}

/**
 * Compact observability summary: deploys-per-day and failure-rate
 * sparklines computed from the recent deploy stream, plus a count of
 * unresolved alert events with a link to the alerts page. Deep-links to
 * `/metrics/<app>` and `/alerts?app=<slug>` for the full surface.
 */
const DAYS_WINDOW = 14;

export function ObservabilitySection({ appSlug }: Props) {
  const deps = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug, limit: 100 },
    fetchPolicy: "cache-and-network",
  });
  const alerts = useQuery<AlertEventsResp>(LIST_ALERT_EVENTS, {
    variables: { unresolvedOnly: true, limit: 50 },
    fetchPolicy: "cache-and-network",
  });

  const days = DAYS_WINDOW;

  const { deploysSeries, errorSeries, totalDeploys, failedCount } = useMemo(() => {
    return buildSeries(deps.data?.astroliftDeployments ?? [], days);
  }, [deps.data, days]);

  const unresolved = alerts.data?.astroliftAlertEvents ?? [];
  const critical = unresolved.filter((a) => a.severity === "critical").length;

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
            href={`/metrics?app=${appSlug}`}
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
      {deps.loading && totalDeploys === 0 ? (
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
            unresolved={unresolved.length}
            critical={critical}
            appSlug={appSlug}
            loading={alerts.loading}
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

function buildSeries(deployments: AstroliftDeployment[], days: number) {
  const buckets: Map<string, { total: number; failed: number }> = new Map();
  // Seed each bucket so the chart spans the full window even when there
  // were quiet days — sparklines look broken when they only have data
  // points at the start and end.
  const today = startOfDay(new Date());
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    buckets.set(d.toISOString(), { total: 0, failed: 0 });
  }

  let totalDeploys = 0;
  let failedCount = 0;
  for (const d of deployments) {
    const key = startOfDay(new Date(d.createdAt)).toISOString();
    const cur = buckets.get(key);
    if (!cur) continue;
    cur.total += 1;
    totalDeploys += 1;
    if (d.status === "failed") {
      cur.failed += 1;
      failedCount += 1;
    }
  }

  const deploysSeries: SparkPoint[] = [];
  const errorSeries: SparkPoint[] = [];
  for (const [ts, v] of buckets) {
    deploysSeries.push({ ts, value: v.total });
    errorSeries.push({ ts, value: v.failed });
  }
  return { deploysSeries, errorSeries, totalDeploys, failedCount };
}

function startOfDay(d: Date): Date {
  const c = new Date(d);
  c.setHours(0, 0, 0, 0);
  return c;
}

function formatPct(v: number): string {
  return `${Math.round(v * 100)}%`;
}
