"use client";

import { useQuery } from "@apollo/client/react";

import { useListState } from "@/components/list/use-list-state";
import { LIST_APP_PODS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppPod } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_WORKLOADS_PAGE } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import { APP_WORKLOADS_LIST, appWorkloadsVariables } from "./workloads-list";

interface WorkloadsPageResp {
  astroliftWorkloadsPage: { items: AstroliftWorkload[]; totalCount: number | null };
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
 * The app's workloads as the Workloads tab's list: list state in the URL
 * (the kind chip is `?kind=`, where `/jobs` lands with `cronjob`), one
 * numbered page of `astroliftWorkloadsPage` filtered, sorted and counted on
 * the server, and live pod state for the readiness and restart cells. The
 * data half of WorkloadsListScreen.
 *
 * `app/(app)/apps/[slug]/workloads/page.tsx` preloads the default page
 * (`appWorkloadsVariables` of the default list state); keep the two in step.
 */
export function useWorkloadsList(slug: string) {
  const list = useListState(APP_WORKLOADS_LIST);
  const { state } = list;

  const workloads = useQuery<WorkloadsPageResp>(LIST_WORKLOADS_PAGE, {
    variables: appWorkloadsVariables(slug, {
      q: state.q,
      filters: list.filters,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    }),
    fetchPolicy: "cache-and-network",
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

  const page = (workloads.data ?? workloads.previousData)?.astroliftWorkloadsPage;
  const rows = page?.items ?? [];
  const liveStatus = aggregatePodStatus(rows, pods.data?.astroliftAppPods ?? []);

  return {
    list,
    rows,
    totalCount: page?.totalCount ?? rows.length,
    loading: workloads.loading && !page,
    error: workloads.error && !page ? { message: workloads.error.message } : null,
    onRetry: () => {
      void workloads.refetch();
    },
    liveStatus,
  };
}
