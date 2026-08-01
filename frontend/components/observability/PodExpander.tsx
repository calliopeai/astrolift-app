"use client";

/**
 * PodExpander — per-pod detail row that renders inline below the
 * selected pod (#713).
 *
 * Contents:
 *
 * * Twin CPU + memory sparkline (live, last 1h by default) sourced
 *   from the per-pod ``astroliftPodResourceUsage`` resolver.
 * * Restart count + last-restart timestamp (when the driver SDK
 *   exposes it — null today; falls back to a "—" affordance).
 * * "Open shell" deep-link to ``/apps/[slug]/shell?pod=…`` so an
 *   operator drops straight into an interactive shell scoped to this
 *   pod. (Was ``/console`` until the shell moved to Control — #1247.)
 *
 * Empty-state: when Prometheus isn't reachable or the pod's series
 * has dropped (terminated pod), the panel renders a "metrics not
 * flowing" callout but keeps the Open shell + restart-count
 * affordances rendering — those don't depend on Prometheus.
 */

import { useQuery } from "@apollo/client/react";
import { ChartSplineIcon, RotateCcwIcon, TerminalIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  type ObservabilityPanelReason,
  panelEmptyState,
} from "@/components/observability/panel-reason";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { GET_POD_RESOURCE_USAGE } from "@/graphql/observability/observability.queries";
import type {
  AstroliftPodResourceUsage,
  AstroliftPodResourceUsagePoint,
} from "@/graphql/__generated__/schema";

interface UsageResp {
  astroliftPodResourceUsage:
    | (AstroliftPodResourceUsage & { reason: ObservabilityPanelReason })
    | null;
}

export interface PodExpanderProps {
  appSlug: string;
  /** Pod name to scope the queries to. */
  podName: string;
  /** Environment to scope the queries to. Null rolls up across envs. */
  environmentName: string | null;
  /** Container to deep-link the Open shell action at. Falls back to
   *  the resolver's heuristic default when null. */
  defaultContainer: string | null;
  /** Fallback restart count from the parent table row so the panel
   *  has something to render before / instead of the per-pod query.
   *  When the per-pod resolver returns its own count it wins. */
  fallbackRestartCount: number;
}

const DEFAULT_RANGE_SECONDS = 60 * 60;

export function PodExpander({
  appSlug,
  podName,
  environmentName,
  defaultContainer,
  fallbackRestartCount,
}: PodExpanderProps) {
  const q = useQuery<UsageResp>(GET_POD_RESOURCE_USAGE, {
    variables: {
      appSlug,
      podName,
      environmentName,
      rangeSeconds: DEFAULT_RANGE_SECONDS,
    },
    // Poll every 30s so an operator watching the panel sees the
    // sparkline tick along without manual refresh. 30s matches the
    // cadence kube-state-metrics scrapes by default.
    pollInterval: 30000,
    fetchPolicy: "cache-and-network",
  });

  const data = q.data?.astroliftPodResourceUsage ?? null;
  const samples = data?.samples ?? [];
  const restartCount = data?.restartCount ?? fallbackRestartCount;
  const podEmpty = panelEmptyState(data?.reason ?? "NOT_CONFIGURED", {
    thing: "pod metrics",
    notConfigured: "Prometheus endpoint isn't wired for this cluster.",
    provider: "this cloud",
  });
  const lastRestartAt = data?.lastRestartAt ?? null;
  const isLoading = q.loading && !q.data;
  const hasSamples = samples.length > 0;

  const cpuRows = React.useMemo(() => samplesToCpuRows(samples), [samples]);
  const memRows = React.useMemo(() => samplesToMemRows(samples), [samples]);

  const shellHref = buildShellHref({
    appSlug,
    podName,
    container: defaultContainer,
    environmentName,
  });

  return (
    <div className="bg-muted/20 space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-muted-foreground inline-flex items-center gap-3 text-xs">
          <span className="inline-flex items-center gap-1">
            <RotateCcwIcon className="size-3" />
            <strong className="text-foreground tabular-nums">{restartCount}</strong>
            {restartCount === 1 ? " restart" : " restarts"}
          </span>
          <span>·</span>
          <span>
            Last restart:{" "}
            <span className="text-foreground tabular-nums">
              {lastRestartAt ? new Date(lastRestartAt).toLocaleString() : "—"}
            </span>
          </span>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href={shellHref}>
            <TerminalIcon className="size-3" />
            Open shell
          </Link>
        </Button>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : !data || !hasSamples ? (
        <div className="text-muted-foreground py-6 text-center text-xs">
          <p className="font-medium">{podEmpty.title}</p>
          <p className="mt-1">{podEmpty.description}</p>
          {data?.reason === "ERROR" && (
            <Button size="sm" variant="outline" className="mt-3" onClick={() => void q.refetch()}>
              Try again
            </Button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <MiniChart
            title="CPU"
            description="Per-pod cores in use (1m rate)."
            rows={cpuRows}
            color="var(--chart-4)"
            yFormatter={(v) => `${v.toFixed(2)} cores`}
          />
          <MiniChart
            title="Memory"
            description="Per-pod working-set bytes (OOM accounting)."
            rows={memRows}
            color="var(--chart-5)"
            yFormatter={(v) => formatBytes(v)}
          />
        </div>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------

interface ChartRow {
  ts: string;
  value: number;
}

function samplesToCpuRows(samples: AstroliftPodResourceUsagePoint[]): ChartRow[] {
  return samples.map((s) => ({
    ts: new Date(s.ts).toLocaleTimeString(),
    value: s.cpuCores,
  }));
}

function samplesToMemRows(samples: AstroliftPodResourceUsagePoint[]): ChartRow[] {
  return samples.map((s) => ({
    ts: new Date(s.ts).toLocaleTimeString(),
    value: s.memoryBytes,
  }));
}

function buildShellHref(args: {
  appSlug: string;
  podName: string;
  container: string | null;
  environmentName: string | null;
}): string {
  const params = new URLSearchParams();
  params.set("pod", args.podName);
  if (args.container) params.set("container", args.container);
  if (args.environmentName) params.set("env", args.environmentName);
  return `/apps/${args.appSlug}/shell?${params.toString()}`;
}

function formatBytes(v: number): string {
  if (v <= 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const i = Math.min(Math.floor(Math.log2(v) / 10), units.length - 1);
  return `${(v / 2 ** (i * 10)).toFixed(2)} ${units[i]}`;
}

interface MiniChartProps {
  title: string;
  description: string;
  rows: ChartRow[];
  color: string;
  yFormatter: (v: number) => string;
}

function MiniChart({ title, description, rows, color, yFormatter }: MiniChartProps) {
  return (
    <div className="bg-background rounded-md border p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <ChartSplineIcon className="text-muted-foreground size-3.5" /> {title}
      </div>
      <p className="text-muted-foreground mt-1 text-xs">{description}</p>
      <div className="mt-2 h-28">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="ts"
              tick={{ fontSize: 10 }}
              tickFormatter={(ts: string) => {
                const parts = ts.split(":");
                return parts.length >= 2 ? `${parts[0]}:${parts[1]}` : ts;
              }}
              minTickGap={32}
            />
            <YAxis tick={{ fontSize: 10 }} tickFormatter={yFormatter} width={64} />
            <Tooltip
              formatter={(v: number) => yFormatter(v)}
              labelFormatter={(label: string) => label}
            />
            <Line
              type="monotone"
              dataKey="value"
              stroke={color}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
