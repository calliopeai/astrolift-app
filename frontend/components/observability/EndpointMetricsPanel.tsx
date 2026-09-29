"use client";

import { BarChart2Icon } from "lucide-react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { selectRows } from "@/components/list/select-rows";
import { useLocalListState } from "@/components/list/use-list-state";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { AstroliftAppEndpointMetric } from "@/graphql/__generated__/schema";

import { ENDPOINT_METRICS_LIST, ENDPOINT_METRICS_SELECT } from "./endpoint-metrics-list";

function formatMs(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(1)}ms`;
}

function errorClass(ratio: number): string {
  const pct = ratio * 100;
  if (pct > 5) return "text-destructive font-medium";
  if (pct > 1) return "text-warning-fg font-medium";
  return "text-muted-foreground";
}

const COLUMNS: Column<AstroliftAppEndpointMetric>[] = [
  {
    id: "route",
    header: "Route",
    sortKey: "route",
    cellClassName: "max-w-80",
    cell: (row) => (
      <code
        className="bg-muted block max-w-full truncate rounded px-1.5 py-0.5 font-mono text-xs"
        title={row.route}
      >
        {row.route}
      </code>
    ),
  },
  {
    id: "rate",
    header: "Req/s",
    sortKey: "rate",
    align: "right",
    cellClassName: "font-mono text-xs",
    cell: (row) => row.requestRate.toFixed(2),
  },
  {
    id: "error",
    header: "Error %",
    sortKey: "error",
    align: "right",
    cell: (row) => (
      <span className={`font-mono text-xs ${errorClass(row.errorRateRatio)}`}>
        {(row.errorRateRatio * 100).toFixed(2)}%
      </span>
    ),
  },
  {
    id: "p50",
    header: "P50",
    sortKey: "p50",
    align: "right",
    cellClassName: "font-mono text-xs",
    cell: (row) => formatMs(row.p50Ms),
  },
  {
    id: "p90",
    header: "P90",
    sortKey: "p90",
    align: "right",
    cellClassName: "font-mono text-xs",
    cell: (row) => formatMs(row.p90Ms),
  },
  {
    id: "p99",
    header: "P99",
    sortKey: "p99",
    align: "right",
    cellClassName: "font-mono text-xs",
    cell: (row) => formatMs(row.p99Ms),
  },
];

/**
 * Request rate, error ratio and latency quantiles per route, as the card's
 * embedded list (busiest first; search, an error filter, sort and numbered
 * pages over the metric set in hand), with its list state kept in the card.
 * Pure (Storybook first): the metrics come from useEndpointMetrics.
 */
export function EndpointMetricsPanel({
  metrics,
  loading,
}: {
  metrics: AstroliftAppEndpointMetric[];
  loading: boolean;
}) {
  const list = useLocalListState(ENDPOINT_METRICS_LIST);
  const all = metrics ?? [];
  const page = selectRows(
    all,
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    ENDPOINT_METRICS_SELECT
  );

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
        <ListPage<AstroliftAppEndpointMetric>
          embedded
          list={list}
          label="Endpoints"
          columns={COLUMNS}
          rows={page.rows}
          getRowId={(r) => r.route}
          loading={loading && all.length === 0}
          totalCount={page.totalCount}
          empty={{
            icon: <BarChart2Icon className="size-5" />,
            title: "No endpoint data",
            description: "OTEL instrumentation not detected.",
          }}
        />
      </CardContent>
    </Card>
  );
}
