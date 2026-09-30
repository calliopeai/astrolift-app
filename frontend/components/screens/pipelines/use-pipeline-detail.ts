"use client";

import { gql } from "@apollo/client";
import { useQuery } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import type { PipelineDagStage } from "@/components/viz";

const GET_PIPELINE_RUNS = gql`
  query GetPipelineRuns($pipelineId: String!, $limit: Int) {
    astroliftPipelineRuns(pipelineId: $pipelineId, limit: $limit) {
      id
      runNumber
      status
      triggerKind
      triggerRef
      startedAt
      finishedAt
    }
  }
`;

// A single run's job graph — jobs are the DAG nodes, `job.needs` the edges.
const GET_PIPELINE_RUN_GRAPH = gql`
  query GetPipelineRunGraph($id: String!) {
    astroliftPipelineRun(id: $id) {
      id
      runNumber
      status
      jobRuns {
        id
        status
        startedAt
        finishedAt
        job {
          jobId
          name
          needs
        }
      }
    }
  }
`;

interface JobRunNode {
  id: string;
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
  job: { jobId: string; name: string; needs: unknown };
}

interface RunGraphResp {
  astroliftPipelineRun: {
    id: string;
    runNumber: number;
    status: string;
    jobRuns: JobRunNode[];
  } | null;
}

export type PipelineDetailTab = "runs" | "logs" | "artifacts" | "triggers" | "runners" | "secrets";
export const PIPELINE_DETAIL_TABS: readonly PipelineDetailTab[] = [
  "runs",
  "logs",
  "artifacts",
  "triggers",
  "runners",
  "secrets",
];

export interface PipelineDetailRun {
  id: string;
  runNumber: number;
  status: string;
  triggerKind: string;
  triggerRef: string;
  startedAt: string | null;
  finishedAt: string | null;
}

/**
 * The data half of PipelineDetailScreen: the `?tab=` state and the run list
 * behind the Runs tab, polled every 10s while that tab is open.
 */
export function usePipelineDetail(pipelineId: string) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as PipelineDetailTab | null;
  const tab: PipelineDetailTab =
    rawTab && PIPELINE_DETAIL_TABS.includes(rawTab)
      ? rawTab
      : pathname.endsWith("/secrets")
        ? "secrets"
        : "runs";

  function onTabChange(next: PipelineDetailTab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "runs") params.delete("tab");
    else params.set("tab", next);
    router.replace(`/pipelines/${pipelineId}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  const {
    data: runsData,
    loading: runsLoading,
    error: runsError,
    refetch: refetchRuns,
  } = useQuery<{
    astroliftPipelineRuns: PipelineDetailRun[];
  }>(GET_PIPELINE_RUNS, {
    variables: { pipelineId, limit: 50 },
    skip: tab !== "runs",
    fetchPolicy: "cache-and-network",
    pollInterval: tab === "runs" ? 10000 : 0,
  });

  const runs = runsData?.astroliftPipelineRuns ?? [];

  return {
    tab,
    onTabChange,
    runs,
    runsLoading: runsLoading && !runsData,
    runsError: runsData ? null : (runsError ?? null),
    onRetryRuns: () => {
      void refetchRuns().catch(() => {});
    },
  };
}

/**
 * A single run's jobs as DAG stages. Jobs are nodes; `job.needs` (a JSON
 * string array) supplies the edges. Polls while the run is in flight so
 * the graph tracks live status.
 */
export function useRunGraph(runId: string) {
  const { data, loading } = useQuery<RunGraphResp>(GET_PIPELINE_RUN_GRAPH, {
    variables: { id: runId },
    fetchPolicy: "cache-and-network",
    pollInterval: 10000,
  });

  const run = data?.astroliftPipelineRun ?? null;

  const stages: PipelineDagStage[] = (run?.jobRuns ?? []).map((jr) => ({
    id: jr.job.jobId,
    name: jr.job.name,
    status: jr.status,
    // `needs` is a JSON scalar — coerce defensively to a string array.
    needs: Array.isArray(jr.job.needs) ? jr.job.needs.map(String) : [],
    startedAt: jr.startedAt,
    finishedAt: jr.finishedAt,
  }));

  /** True only for the first fetch: a poll keeps the graph on screen. */
  const initialLoading = loading && !run;

  return { loading: initialLoading, stages };
}
