"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import {
  GET_APP_URL_HEALTH,
  GET_APP_URL_PROBE_HISTORY,
} from "@/graphql/observability/observability.queries";

// Match the wire enum on `AstroliftAppUrlHealth.status`. Kept narrow
// here (rather than imported from the codegen output) so this
// component compiles against any frontend that has had codegen run
// at least once — useful in worktrees where codegen hasn't been
// pulled fresh.
export type UrlHealthStatus = "ok" | "degraded" | "down" | "unknown";

export interface UrlHealthRow {
  url: string;
  status: UrlHealthStatus | string;
  statusCode: number | null;
  latencyMs: number | null;
  lastChecked: string;
  message: string;
}

interface HealthResp {
  astroliftAppUrlHealth: UrlHealthRow | null;
}

interface HistoryResp {
  astroliftAppUrlProbeHistory: UrlHealthRow[];
}

// 30s matches the backend cache TTL. A poll faster than this just
// reads the cached payload server-side; slower than this and we
// leave operators staring at stale pills.
const POLL_INTERVAL_MS = 30_000;

// Treat anything older than this as worth re-fetching even on first
// mount. The cache key on the server is per (app, url), so two
// operators on the same overview share the work.

/**
 * Data half of UrlHealthBadgeView (#406).
 *
 * Polls `astroliftAppUrlHealth` every 30s while the page is visible
 * (suspends polling when the tab is hidden — no point burning probes
 * for an off-screen overview). `onRecheck` issues an immediate
 * re-probe with `forceRefresh: true`. The last-5 probe history is
 * fetched only while the tooltip is open.
 */
export function useUrlHealth({ appSlug, url }: { appSlug: string; url: string }) {
  const [isVisible, setIsVisible] = React.useState(
    () => typeof document === "undefined" || document.visibilityState !== "hidden"
  );
  const [historyOpen, setHistoryOpen] = React.useState(false);

  // Suspend polling when the tab is hidden so we don't keep hitting
  // the operator's apps from a backgrounded tab. The browser fires
  // visibilitychange synchronously when the tab gains/loses focus —
  // we flip pollInterval to 0 in the off state, which Apollo treats
  // as "no polling".
  React.useEffect(() => {
    if (typeof document === "undefined") return;
    const onChange = () => setIsVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  const { data, loading, refetch } = useQuery<HealthResp>(GET_APP_URL_HEALTH, {
    variables: { appSlug, url, forceRefresh: false },
    fetchPolicy: "cache-and-network",
    pollInterval: isVisible ? POLL_INTERVAL_MS : 0,
    notifyOnNetworkStatusChange: true,
  });

  // History query — fetched lazily on tooltip open (Apollo won't
  // issue a network round-trip until the query mounts because we
  // pass `skip` based on the tooltip's open state).
  const historyQ = useQuery<HistoryResp>(GET_APP_URL_PROBE_HISTORY, {
    variables: { appSlug, url, limit: 5 },
    fetchPolicy: "cache-and-network",
    skip: !historyOpen,
  });

  const health = data?.astroliftAppUrlHealth;

  async function onRecheck() {
    // forceRefresh tells the backend to skip its 30s result cache so
    // the operator immediately sees a fresh probe.
    await refetch({ appSlug, url, forceRefresh: true });
  }

  return {
    health,
    // `loading` flips true on both first-mount and pollInterval ticks;
    // the spinner is only for the first-ever load (no data yet).
    isInitialLoading: loading && !health,
    rechecking: loading && !!health,
    history: historyQ.data?.astroliftAppUrlProbeHistory ?? [],
    loadingHistory: historyQ.loading,
    onHistoryOpenChange: setHistoryOpen,
    onRecheck,
  };
}
