"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import {
  GET_WORKLOAD_POD_STATUS_BREAKDOWN,
  LIST_APP_PODS,
  LIST_SCHEDULED_JOB_RUNS,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppPod,
  AstroliftContainerStatus,
  AstroliftScheduledJobRun,
  AstroliftWorkloadPodStatusBucket,
} from "@/graphql/lifecycle/lifecycle.types";
import { GET_WORKLOAD, LIST_CONTAINERS } from "@/graphql/registry/registry.queries";
import type { AstroliftContainer, AstroliftWorkload } from "@/graphql/registry/registry.types";

interface WorkloadResp {
  astroliftWorkload: AstroliftWorkload | null;
}

interface ContainersResp {
  astroliftContainers: AstroliftContainer[];
}

interface JobRunsResp {
  astroliftScheduledJobRuns: AstroliftScheduledJobRun[];
}

interface PodsResp {
  astroliftAppPods: AstroliftAppPod[];
}

interface BreakdownResp {
  astroliftWorkloadPodStatusBreakdown: AstroliftWorkloadPodStatusBucket[];
}

// Refresh cadence for the live pod surface (status grid + pod table).
// Matches the observability page's pod poll so a user toggling between
// them sees the same data on the same cadence.
const POD_POLL_MS = 15_000;

/**
 * The workload, its manifest containers, its live pods and status buckets,
 * and (cronjobs only) its recent scheduled runs. The data half of
 * WorkloadDetailScreen; the resource, scaling and manifest cards fetch
 * their own.
 */
export function useWorkloadDetail(appSlug: string, workloadSlug: string) {
  const { data: wlData, loading: wlLoading } = useQuery<WorkloadResp>(GET_WORKLOAD, {
    variables: { appSlug, slug: workloadSlug },
    fetchPolicy: "cache-and-network",
  });
  const { data: cData, loading: cLoading } = useQuery<ContainersResp>(LIST_CONTAINERS, {
    variables: { workloadSlug },
    fetchPolicy: "cache-and-network",
  });
  const w = wlData?.astroliftWorkload ?? null;

  // Live pod state — drives the restart column + sidecar resource
  // cards. Filtered to *this* workload's pods only so a noisy
  // sibling workload doesn't drown the table.
  const { data: pdData, loading: pdLoading } = useQuery<PodsResp>(LIST_APP_PODS, {
    variables: { appSlug },
    pollInterval: POD_POLL_MS,
    fetchPolicy: "cache-and-network",
  });

  const { data: brData, loading: brLoading } = useQuery<BreakdownResp>(
    GET_WORKLOAD_POD_STATUS_BREAKDOWN,
    {
      variables: { appSlug, workloadSlug },
      pollInterval: POD_POLL_MS,
      fetchPolicy: "cache-and-network",
    }
  );

  const isCronjob = w?.kind === "cronjob";
  const { data: rData, loading: rLoading } = useQuery<JobRunsResp>(LIST_SCHEDULED_JOB_RUNS, {
    variables: { appSlug, limit: 10 },
    skip: !isCronjob,
    pollInterval: isCronjob ? 15000 : 0,
    fetchPolicy: "cache-and-network",
  });

  const containers = cData?.astroliftContainers ?? [];
  const runs = (rData?.astroliftScheduledJobRuns ?? []).filter(
    (r) => r.workloadSlug === workloadSlug
  );

  const podRows: AstroliftAppPod[] = React.useMemo(
    () => (pdData?.astroliftAppPods ?? []).filter((p) => (p.workload || "") === workloadSlug),
    [pdData, workloadSlug]
  );
  const buckets = brData?.astroliftWorkloadPodStatusBreakdown ?? [];

  // Group containers by kind for the Init / Primary / Sidecar split.
  // Source of truth is the *live* pod data because the manifest-side
  // container row doesn't carry the sidecar/init classification (it
  // mirrors what the operator declared, not what kubernetes ended up
  // running with injected sidecars like istio-proxy).
  const liveContainers: AstroliftContainerStatus[] = podRows
    .flatMap((p) => p.containerStatuses)
    // Dedupe by name — multiple pods report the same containers.
    .reduce<AstroliftContainerStatus[]>((acc, cs) => {
      if (acc.some((c) => c.name === cs.name)) return acc;
      acc.push(cs);
      return acc;
    }, []);

  return {
    workload: w,
    workloadLoading: wlLoading,
    containers,
    containersLoading: cLoading,
    pods: podRows,
    podsLoading: pdLoading,
    buckets,
    bucketsLoading: brLoading,
    liveContainers,
    isCronjob,
    runs,
    runsLoading: rLoading,
  };
}
