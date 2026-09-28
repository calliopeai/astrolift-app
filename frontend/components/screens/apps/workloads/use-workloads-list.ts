"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { useCursorTable } from "@/components/data-table";
import { LIST_APP_PODS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppPod } from "@/graphql/lifecycle/lifecycle.types";
import { GET_APP, LIST_WORKLOADS, LIST_WORKLOADS_PAGE } from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  AstroliftWorkload,
  WorkloadKind,
} from "@/graphql/registry/registry.types";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}
interface WorkloadsPageResp {
  astroliftWorkloadsPage: {
    items: AstroliftWorkload[];
    nextCursor?: string | null;
    totalCount?: number | null;
  };
}
interface AppPodsResp {
  astroliftAppPods: AstroliftAppPod[];
}

export interface WorkloadLiveStatus {
  ready: number;
  desired: number;
  maxRestarts: number;
  // #666 — surfaces the most recent K8s warning event across the
  // workload's pods so operators see ImagePullBackOff /
  // CrashLoopBackOff / OOMKilled inline without drilling into the
  // pod's detail page.
  errorEvent: {
    reason: string;
    message: string;
    count: number;
    lastSeen: string;
  } | null;
}

/**
 * Aggregate per-workload runtime counts from the flat pod list.
 *
 * `ready` = pods in phase `Running` (case-insensitive — the cluster
 * resolver emits "Running" but we lowercase to defend against driver
 * drift). `desired` falls through to the manifest-declared replica
 * count when the pod list yields fewer rows than declared (e.g. a
 * fresh deploy still spinning up). `maxRestarts` is the **max**
 * restart_count across pods in the workload — that matches what an
 * operator wants to see ("which workload is flapping?"), not a sum.
 *
 * Runs over the workloads on the current page only; the pod list is
 * app-wide, so a page's rows always find their pods.
 */
function aggregatePodStatus(
  workloads: AstroliftWorkload[],
  pods: AstroliftAppPod[]
): Map<string, WorkloadLiveStatus> {
  const byWorkload = new Map<string, AstroliftAppPod[]>();
  for (const p of pods) {
    const arr = byWorkload.get(p.workload) ?? [];
    arr.push(p);
    byWorkload.set(p.workload, arr);
  }
  const result = new Map<string, WorkloadLiveStatus>();
  for (const w of workloads) {
    const pp = byWorkload.get(w.slug) ?? [];
    const ready = pp.filter((p) => (p.phase || "").toLowerCase() === "running").length;
    const maxRestarts = pp.reduce((acc, p) => Math.max(acc, p.restarts ?? 0), 0);
    // #666 — pick the most-recent error event across pods so the
    // chip surfaces what's actually breaking. `recentErrorEvent`
    // is null on healthy pods.
    let errorEvent: WorkloadLiveStatus["errorEvent"] = null;
    for (const p of pp) {
      const ev = (
        p as AstroliftAppPod & {
          recentErrorEvent?: {
            reason: string;
            message: string;
            count: number;
            lastSeen: string;
          } | null;
        }
      ).recentErrorEvent;
      if (!ev) continue;
      if (!errorEvent || Date.parse(ev.lastSeen) > Date.parse(errorEvent.lastSeen)) {
        errorEvent = ev;
      }
    }
    result.set(w.slug, {
      ready,
      desired: Math.max(w.replicas || 0, pp.length),
      maxRestarts,
      errorEvent,
    });
  }
  return result;
}

/**
 * The app, its workload table (server-paged), the whole-set summary behind
 * the stats strip, and live pod state for the readiness and restart cells.
 * The data half of WorkloadsListScreen.
 */
export function useWorkloadsList(slug: string) {
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });

  /**
   * The stats strip summarises the app's *whole* workload set, and
   * `astroliftWorkloadsPage` exposes no aggregate — summing the page in
   * hand would make "Total replicas" quietly mean "on this page". So the
   * strip stays on the flat field while the table below pages on the
   * server. Backend ask: a workload-summary field (or kind / public
   * filters with `totalCount`) would retire this second round trip.
   */
  const summary = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug: slug },
    pollInterval: 60000,
  });
  // Live pod state for the readiness + restart-count cells. Pulled on a
  // 30s tick so an operator watching a rollout sees ready/desired
  // converge without page reloads.
  const pods = useQuery<AppPodsResp>(LIST_APP_PODS, {
    variables: { appSlug: slug },
    pollInterval: 30000,
    fetchPolicy: "cache-and-network",
  });

  // `astroliftWorkloadsPage` filters on `appSlug` and searches name,
  // slug, kind and app slug. It takes no sort argument, so no column
  // declares a `sortKey` — sorting the page in hand while the rest of
  // the set sits on the server is wrong at every page boundary.
  const table = useCursorTable<AstroliftWorkload>({
    query: LIST_WORKLOADS_PAGE,
    variables: { appSlug: slug },
    extract: (d) => (d as WorkloadsPageResp | undefined)?.astroliftWorkloadsPage,
    searchVariable: "search",
    urlKey: "wl",
    pollInterval: 60000,
  });

  const summaryList = React.useMemo(
    () => summary.data?.astroliftWorkloads ?? [],
    [summary.data?.astroliftWorkloads]
  );
  const podList = React.useMemo(
    () => pods.data?.astroliftAppPods ?? [],
    [pods.data?.astroliftAppPods]
  );
  const liveStatus = React.useMemo(
    () => aggregatePodStatus(table.rows, podList),
    [table.rows, podList]
  );

  // Surface stats up top so an operator immediately sees the shape of
  // the app's workload set before diving into rows.
  const totalReplicas = summaryList.reduce((acc, w) => acc + (w.replicas || 0), 0);
  const publicCount = summaryList.filter((w) => w.isPublic).length;
  const kindCounts = summaryList.reduce<Partial<Record<WorkloadKind, number>>>((acc, w) => {
    acc[w.kind] = (acc[w.kind] ?? 0) + 1;
    return acc;
  }, {});

  return {
    app: app.data?.astroliftApp ?? null,
    appLoading: app.loading,
    table,
    liveStatus,
    stats: {
      workloads: table.totalCount ?? summaryList.length,
      totalReplicas,
      publicCount,
      scheduled: kindCounts.cronjob ?? 0,
    },
  };
}
