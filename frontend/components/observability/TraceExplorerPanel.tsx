"use client";

import { useLazyQuery, useQuery } from "@apollo/client/react";
import { ChevronDownIcon, ChevronRightIcon, GitBranchIcon } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { GET_APP_TRACES, GET_TRACE_SPANS } from "@/graphql/observability/observability.queries";
import type { AstroliftAppTrace, AstroliftTraceSpan } from "@/graphql/__generated__/schema";

interface TracesResp {
  astroliftAppTraces: AstroliftAppTrace[];
}

interface SpansResp {
  astroliftTraceSpans: AstroliftTraceSpan[];
}

export interface TraceExplorerPanelProps {
  appSlug: string;
  environmentName?: string | null;
}

type StatusFilter = "ALL" | "OK" | "ERROR";

const TRACE_LOOKBACK_SECONDS = 3600;
const TRACE_LIMIT = 50;

export function TraceExplorerPanel({ appSlug, environmentName }: TraceExplorerPanelProps) {
  const [statusFilter, setStatusFilter] = React.useState<StatusFilter>("ALL");

  // Snapshot the range on mount via lazy state init so the query
  // variables stay stable across re-renders (Date.now is impure and
  // can't be called during render).
  const [{ since, until }] = React.useState(() => {
    const nowSec = Math.floor(Date.now() / 1000);
    return {
      since: String(nowSec - TRACE_LOOKBACK_SECONDS),
      until: String(nowSec),
    };
  });

  const { data, loading } = useQuery<TracesResp>(GET_APP_TRACES, {
    variables: {
      appSlug,
      since,
      until,
      environmentName: environmentName ?? null,
      status: statusFilter === "ALL" ? null : statusFilter,
      limit: TRACE_LIMIT,
    },
    fetchPolicy: "cache-and-network",
  });

  const traces = data?.astroliftAppTraces ?? [];
  const isInitialLoading = loading && !data;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <GitBranchIcon className="size-4" /> Traces
            </CardTitle>
            <CardDescription>
              Recent distributed traces from the last 1h. Click a row to expand spans.
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground text-xs">Status</span>
            <Select
              value={statusFilter}
              onValueChange={(v) => setStatusFilter(v as StatusFilter)}
            >
              <SelectTrigger size="sm" className="h-8 w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All</SelectItem>
                <SelectItem value="OK">OK</SelectItem>
                <SelectItem value="ERROR">ERROR</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Root Service</TableHead>
              <TableHead>Root Operation</TableHead>
              <TableHead className="text-right">Spans</TableHead>
              <TableHead className="text-right">Duration</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isInitialLoading ? (
              Array.from({ length: 3 }).map((_, i) => (
                <TableRow key={`skeleton-${i}`}>
                  <TableCell />
                  <TableCell>
                    <Skeleton className="h-4 w-32" />
                  </TableCell>
                  <TableCell>
                    <Skeleton className="h-4 w-48" />
                  </TableCell>
                  <TableCell className="text-right">
                    <Skeleton className="ml-auto h-4 w-8" />
                  </TableCell>
                  <TableCell className="text-right">
                    <Skeleton className="ml-auto h-4 w-12" />
                  </TableCell>
                  <TableCell>
                    <Skeleton className="h-4 w-12" />
                  </TableCell>
                </TableRow>
              ))
            ) : traces.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-muted-foreground py-8 text-center text-sm">
                  No traces — tracing backend not configured
                </TableCell>
              </TableRow>
            ) : (
              traces.map((trace) => (
                <TraceRow
                  key={trace.traceId}
                  trace={trace}
                  appSlug={appSlug}
                  environmentName={environmentName ?? null}
                />
              ))
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

interface TraceRowProps {
  trace: AstroliftAppTrace;
  appSlug: string;
  environmentName: string | null;
}

function TraceRow({ trace, appSlug, environmentName }: TraceRowProps) {
  const [expanded, setExpanded] = React.useState(false);
  const [fetchSpans, spansQuery] = useLazyQuery<SpansResp>(GET_TRACE_SPANS);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    if (next && !spansQuery.called) {
      void fetchSpans({
        variables: {
          appSlug,
          traceId: trace.traceId,
          environmentName,
        },
      });
    }
  };

  const Chevron = expanded ? ChevronDownIcon : ChevronRightIcon;
  // useLazyQuery returns DeepPartial<TData>; coerce here since the whole
  // span shape lands or `data` is undefined (mirrors the settings panel
  // helper).
  const spans = (spansQuery.data?.astroliftTraceSpans as AstroliftTraceSpan[] | undefined) ?? [];

  return (
    <>
      <TableRow
        className="hover:bg-muted/40 cursor-pointer"
        onClick={toggle}
        aria-expanded={expanded}
      >
        <TableCell className="w-8">
          <Chevron className="text-muted-foreground size-4" />
        </TableCell>
        <TableCell className="font-mono text-xs">{trace.rootService}</TableCell>
        <TableCell className="font-mono text-xs">{trace.rootOperation}</TableCell>
        <TableCell className="text-right font-mono text-xs">{trace.spanCount}</TableCell>
        <TableCell className="text-right font-mono text-xs">
          {formatDuration(trace.durationMs)}
        </TableCell>
        <TableCell>
          <StatusBadge code={trace.statusCode} />
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow className="bg-muted/20 hover:bg-muted/20">
          <TableCell colSpan={6} className="py-3">
            <SpanList loading={spansQuery.loading && !spansQuery.data} spans={spans} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

interface SpanListProps {
  loading: boolean;
  spans: AstroliftTraceSpan[];
}

function SpanList({ loading, spans }: SpanListProps) {
  const ordered = React.useMemo(() => orderSpansByDepth(spans), [spans]);

  if (loading) {
    return (
      <div className="space-y-2 px-2">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-4 w-2/3" />
      </div>
    );
  }
  if (spans.length === 0) {
    return (
      <p className="text-muted-foreground px-2 text-center text-xs">
        No spans returned for this trace.
      </p>
    );
  }

  return (
    <div className="space-y-1 px-2">
      {ordered.map(({ span, depth }) => (
        <div
          key={span.spanId}
          className="flex items-center gap-3 text-xs"
          style={{ paddingLeft: `${depth * 16}px` }}
        >
          <span className="font-mono">{span.operation}</span>
          <span className="text-muted-foreground font-mono">{span.service}</span>
          <span className="text-muted-foreground font-mono">
            {formatDuration(span.durationMs)}
          </span>
          <StatusBadge code={span.statusCode} />
        </div>
      ))}
    </div>
  );
}

function StatusBadge({ code }: { code: string }) {
  const upper = code.toUpperCase();
  if (upper === "OK") {
    return (
      <Badge className="bg-success/15 text-success-fg" variant="outline">
        OK
      </Badge>
    );
  }
  if (upper === "ERROR") {
    return <Badge variant="destructive">ERROR</Badge>;
  }
  return (
    <Badge variant="outline" className="text-muted-foreground">
      {upper || "UNSET"}
    </Badge>
  );
}

function formatDuration(ms: number): string {
  if (ms >= 1000) {
    return `${(ms / 1000).toFixed(2)}s`;
  }
  return `${ms.toFixed(1)}ms`;
}

interface OrderedSpan {
  span: AstroliftTraceSpan;
  depth: number;
}

function orderSpansByDepth(spans: AstroliftTraceSpan[]): OrderedSpan[] {
  if (spans.length === 0) return [];
  const byId = new Map<string, AstroliftTraceSpan>();
  const children = new Map<string | null, AstroliftTraceSpan[]>();
  for (const s of spans) {
    byId.set(s.spanId, s);
    const key = s.parentSpanId ?? null;
    const list = children.get(key) ?? [];
    list.push(s);
    children.set(key, list);
  }

  // Roots: spans whose parent is null or whose parent isn't in this
  // batch (e.g., parent lives in another service we didn't fetch).
  const roots: AstroliftTraceSpan[] = [];
  for (const s of spans) {
    if (!s.parentSpanId || !byId.has(s.parentSpanId)) {
      roots.push(s);
    }
  }
  // Stable order: by startTime when available, fallback to spanId.
  const sortFn = (a: AstroliftTraceSpan, b: AstroliftTraceSpan) => {
    if (a.startTime && b.startTime) return a.startTime.localeCompare(b.startTime);
    return a.spanId.localeCompare(b.spanId);
  };
  roots.sort(sortFn);

  const out: OrderedSpan[] = [];
  const walk = (span: AstroliftTraceSpan, depth: number) => {
    out.push({ span, depth });
    const kids = (children.get(span.spanId) ?? []).slice().sort(sortFn);
    for (const k of kids) walk(k, depth + 1);
  };
  for (const r of roots) walk(r, 0);
  return out;
}
