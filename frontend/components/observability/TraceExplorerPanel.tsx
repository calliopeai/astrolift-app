"use client";

import { ChevronDownIcon, ChevronRightIcon, GitBranchIcon } from "lucide-react";
import * as React from "react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { type SelectRowsSpec, selectRows } from "@/components/list/select-rows";
import {
  type ListDefinition,
  standardViews,
  useLocalListState,
} from "@/components/list/use-list-state";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftAppTrace, AstroliftTraceSpan } from "@/graphql/__generated__/schema";

/** One trace's spans, loaded on first expand. */
export interface TraceSpans {
  loading: boolean;
  spans: AstroliftTraceSpan[];
}

export type TraceStatusFilter = "ALL" | "OK" | "ERROR";

export interface TraceExplorerPanelProps {
  traces: AstroliftAppTrace[];
  loading: boolean;
  statusFilter: TraceStatusFilter;
  onStatusFilterChange: (filter: TraceStatusFilter) => void;
  spans: Record<string, TraceSpans>;
  onExpand: (traceId: string) => void;
}

/**
 * The traces as an embedded list. Status is the query's filter (the hook
 * sends it); search, sort and numbered pages run over the bounded window the
 * query returns (needsBackend: a Page field). Traces are the app's, not a
 * person's, so Mine is empty.
 */
const TRACES_LIST: ListDefinition = {
  id: "observability.traces",
  fields: [
    {
      key: "status",
      label: "Status",
      options: [
        { value: "OK", label: "OK" },
        { value: "ERROR", label: "ERROR" },
      ],
    },
  ],
  searchPlaceholder: "Search services, operations, trace ids…",
  defaultSort: [{ key: "recent", dir: "asc" }],
  views: standardViews({ owner: "me" }, [], {
    mineNote: "Traces are the app's, not a person's, so Mine is empty.",
  }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

function tracesSelect(traces: AstroliftAppTrace[]): SelectRowsSpec<AstroliftAppTrace> {
  // The query's own order (most recent first) is the default.
  const order = new Map(traces.map((t, i) => [t.traceId, i]));
  return {
    filter: { owner: () => false },
    text: (t) => [t.rootService, t.rootOperation, t.traceId],
    sort: {
      recent: (t) => order.get(t.traceId) ?? 0,
      service: (t) => t.rootService.toLowerCase(),
      operation: (t) => t.rootOperation.toLowerCase(),
      spans: (t) => t.spanCount,
      duration: (t) => t.durationMs,
    },
    id: (t) => t.traceId,
  };
}

/** Pure (Storybook first): traces, filter and spans come from useTraceExplorer. */
export function TraceExplorerPanel({
  traces,
  loading: isInitialLoading,
  statusFilter,
  onStatusFilterChange,
  spans,
  onExpand,
}: TraceExplorerPanelProps) {
  const list = useLocalListState(TRACES_LIST, {
    filters: statusFilter === "ALL" ? {} : { status: statusFilter },
  });
  const status = list.filters.status;
  // The status chip is the query's filter: hand it to the hook.
  React.useEffect(() => {
    onStatusFilterChange(status === "OK" || status === "ERROR" ? status : "ALL");
  }, [status, onStatusFilterChange]);

  const [expanded, setExpanded] = React.useState<ReadonlySet<string>>(() => new Set());
  const toggle = (traceId: string) => {
    const open = !expanded.has(traceId);
    setExpanded((prev) => {
      const next = new Set(prev);
      if (open) next.add(traceId);
      else next.delete(traceId);
      return next;
    });
    if (open) onExpand(traceId);
  };

  const page = selectRows(
    traces,
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    tracesSelect(traces)
  );

  const columns: Column<AstroliftAppTrace>[] = [
    {
      id: "operation",
      header: "Root Operation",
      sortKey: "operation",
      cellClassName: "min-w-0",
      cell: (trace) => {
        const open = expanded.has(trace.traceId);
        const Chevron = open ? ChevronDownIcon : ChevronRightIcon;
        return (
          <div className="flex min-w-0 flex-col gap-2">
            <button
              type="button"
              onClick={() => toggle(trace.traceId)}
              aria-expanded={open}
              className="flex min-w-0 items-center gap-2 text-left"
            >
              <Chevron className="text-muted-foreground size-4 shrink-0" />
              <span className="font-mono text-xs [overflow-wrap:anywhere]">
                {trace.rootOperation}
              </span>
            </button>
            {open && (
              <div className="bg-muted/20 rounded-md py-3">
                <SpanList
                  loading={spans[trace.traceId]?.loading ?? true}
                  spans={spans[trace.traceId]?.spans ?? []}
                />
              </div>
            )}
          </div>
        );
      },
    },
    {
      id: "service",
      header: "Root Service",
      sortKey: "service",
      cellClassName: "align-top font-mono text-xs",
      cell: (trace) => trace.rootService,
    },
    {
      id: "spans",
      header: "Spans",
      sortKey: "spans",
      align: "right",
      cellClassName: "align-top font-mono text-xs",
      cell: (trace) => trace.spanCount,
    },
    {
      id: "duration",
      header: "Duration",
      sortKey: "duration",
      align: "right",
      cellClassName: "align-top font-mono text-xs",
      cell: (trace) => formatDuration(trace.durationMs),
    },
    {
      id: "status",
      header: "Status",
      cellClassName: "align-top",
      cell: (trace) => <StatusBadge code={trace.statusCode} />,
    },
  ];

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <GitBranchIcon className="size-4" /> Traces
        </CardTitle>
        <CardDescription>
          Recent distributed traces from the last 1h. Click an operation to expand its spans.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ListPage<AstroliftAppTrace>
          embedded
          list={list}
          label="Traces"
          columns={columns}
          rows={page.rows}
          getRowId={(t) => t.traceId}
          totalCount={page.totalCount}
          loading={isInitialLoading}
          empty={{
            icon: <GitBranchIcon className="size-5" />,
            title: "No traces",
            description: "The tracing backend is not configured.",
          }}
        />
      </CardContent>
    </Card>
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
          <span className="text-muted-foreground font-mono">{formatDuration(span.durationMs)}</span>
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
