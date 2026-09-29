"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import * as React from "react";

import type { AstroliftAppTrace, AstroliftTraceSpan } from "@/graphql/__generated__/schema";
import { GET_APP_TRACES, GET_TRACE_SPANS } from "@/graphql/observability/observability.queries";

import type { TraceSpans, TraceStatusFilter } from "./TraceExplorerPanel";

const TRACE_LOOKBACK_SECONDS = 3600;
const TRACE_LIMIT = 50;

/**
 * Recent traces (#644) under a status filter, and each trace's spans loaded
 * once when it is first expanded: the data half of TraceExplorerPanel.
 */
export function useTraceExplorer(appSlug: string, environmentName?: string | null) {
  const client = useApolloClient();
  const [statusFilter, setStatusFilter] = React.useState<TraceStatusFilter>("ALL");
  // Snapshot the range on mount so the query variables stay stable across
  // re-renders (Date.now is impure and can't be called during render).
  const [{ since, until }] = React.useState(() => {
    const nowSec = Math.floor(Date.now() / 1000);
    return { since: String(nowSec - TRACE_LOOKBACK_SECONDS), until: String(nowSec) };
  });

  const { data, loading } = useQuery<{ astroliftAppTraces: AstroliftAppTrace[] }>(GET_APP_TRACES, {
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

  const [spans, setSpans] = React.useState<Record<string, TraceSpans>>({});
  const onExpand = React.useCallback(
    (traceId: string) => {
      if (spans[traceId]) return;
      setSpans((prev) => ({ ...prev, [traceId]: { loading: true, spans: [] } }));
      void client
        .query<{ astroliftTraceSpans: AstroliftTraceSpan[] }>({
          query: GET_TRACE_SPANS,
          variables: { appSlug, traceId, environmentName: environmentName ?? null },
        })
        .then(({ data: result }) =>
          setSpans((prev) => ({
            ...prev,
            [traceId]: { loading: false, spans: result?.astroliftTraceSpans ?? [] },
          }))
        )
        .catch(() => setSpans((prev) => ({ ...prev, [traceId]: { loading: false, spans: [] } })));
    },
    [client, appSlug, environmentName, spans]
  );

  return {
    traces: data?.astroliftAppTraces ?? [],
    loading: loading && !data,
    statusFilter,
    onStatusFilterChange: setStatusFilter,
    spans,
    onExpand,
  };
}
