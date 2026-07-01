"use client";

import { useQuery } from "@apollo/client/react";
import { BarChart2Icon } from "lucide-react";
import * as React from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { GET_APP_ENDPOINT_METRICS } from "@/graphql/observability/observability.queries";
import type { AstroliftAppEndpointMetric } from "@/graphql/__generated__/schema";

interface EndpointMetricsResp {
  astroliftAppEndpointMetrics: AstroliftAppEndpointMetric[];
}

export interface EndpointMetricsPanelProps {
  appSlug: string;
  environmentName?: string | null;
  workloadSlug?: string | null;
  rangeSeconds?: number;
}

const DEFAULT_RANGE_SECONDS = 3600;

export function EndpointMetricsPanel({
  appSlug,
  environmentName,
  workloadSlug,
  rangeSeconds = DEFAULT_RANGE_SECONDS,
}: EndpointMetricsPanelProps) {
  const { data, loading } = useQuery<EndpointMetricsResp>(GET_APP_ENDPOINT_METRICS, {
    variables: {
      appSlug,
      environmentName: environmentName ?? null,
      workloadSlug: workloadSlug ?? null,
      rangeSeconds,
    },
    fetchPolicy: "cache-and-network",
    pollInterval: 30_000,
  });

  const rows = React.useMemo(() => {
    const items = data?.astroliftAppEndpointMetrics ?? [];
    return [...items].sort((a, b) => b.requestRate - a.requestRate);
  }, [data]);

  const isInitialLoading = loading && !data;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <BarChart2Icon className="size-4" /> Endpoints
        </CardTitle>
        <CardDescription>
          Request rate, error ratio, and latency quantiles per route. Refreshed every 30s.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Route</TableHead>
              <TableHead className="text-right">Req/s</TableHead>
              <TableHead className="text-right">Error %</TableHead>
              <TableHead className="text-right">P50</TableHead>
              <TableHead className="text-right">P90</TableHead>
              <TableHead className="text-right">P99</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isInitialLoading ? (
              Array.from({ length: 3 }).map((_, i) => (
                <TableRow key={`skeleton-${i}`}>
                  <TableCell>
                    <Skeleton className="h-4 w-40" />
                  </TableCell>
                  <TableCell className="text-right">
                    <Skeleton className="ml-auto h-4 w-12" />
                  </TableCell>
                  <TableCell className="text-right">
                    <Skeleton className="ml-auto h-4 w-12" />
                  </TableCell>
                  <TableCell className="text-right">
                    <Skeleton className="ml-auto h-4 w-12" />
                  </TableCell>
                  <TableCell className="text-right">
                    <Skeleton className="ml-auto h-4 w-12" />
                  </TableCell>
                  <TableCell className="text-right">
                    <Skeleton className="ml-auto h-4 w-12" />
                  </TableCell>
                </TableRow>
              ))
            ) : rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-muted-foreground py-8 text-center text-sm">
                  No endpoint data — OTEL instrumentation not detected
                </TableCell>
              </TableRow>
            ) : (
              rows.map((row) => <EndpointRow key={row.route} row={row} />)
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function EndpointRow({ row }: { row: AstroliftAppEndpointMetric }) {
  const errorPct = row.errorRateRatio * 100;
  let errorClass = "text-muted-foreground";
  if (errorPct > 5) {
    errorClass = "text-destructive font-medium";
  } else if (errorPct > 1) {
    errorClass = "text-warning-fg font-medium";
  }

  return (
    <TableRow>
      <TableCell>
        <code className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">{row.route}</code>
      </TableCell>
      <TableCell className="text-right font-mono text-xs">{row.requestRate.toFixed(2)}</TableCell>
      <TableCell className={`text-right font-mono text-xs ${errorClass}`}>
        {errorPct.toFixed(2)}%
      </TableCell>
      <TableCell className="text-right font-mono text-xs">{formatMs(row.p50Ms)}</TableCell>
      <TableCell className="text-right font-mono text-xs">{formatMs(row.p90Ms)}</TableCell>
      <TableCell className="text-right font-mono text-xs">{formatMs(row.p99Ms)}</TableCell>
    </TableRow>
  );
}

function formatMs(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(1)}ms`;
}
