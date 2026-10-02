"use client";

import { useState, useEffect } from "react";
import { useReviewedPipelineCancel } from "@/components/reviewed-starts/use-reviewed-pipeline-cancel";
import { gql } from "@apollo/client";
import { useQuery, useApolloClient } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { GET_PIPELINE } from "@/graphql/pipelines/pipelines.queries";
import type { AstroliftPipeline } from "@/graphql/pipelines/pipelines.types";

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
      jobsTruncated
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
    jobsTruncated: boolean;
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

  const definition = useQuery<{ astroliftPipeline: AstroliftPipeline | null }>(GET_PIPELINE, {
    variables: { id: pipelineId },
    skip: !pipelineId,
    fetchPolicy: "cache-and-network",
  });

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
  const cancellation = useReviewedPipelineCancel();

  return {
    onCancelRun: cancellation.open,
    cancellationDialog: cancellation.dialog,
    pipeline: definition.data?.astroliftPipeline ?? null,
    pipelineLoading: definition.loading && !definition.data,
    pipelineError: definition.error ?? null,
    onRetryPipeline: () => {
      void definition.refetch().catch(() => {});
    },
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
  const client = useApolloClient();
  const [additional, setAdditional] = useState<JobRunNode[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [pagesLoaded, setPagesLoaded] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [pageError, setPageError] = useState(false);
  useEffect(() => {
    setAdditional([]);
    setNextCursor(null);
    setPagesLoaded(false);
    setPageError(false);
  }, [runId]);
  const { data, loading } = useQuery<RunGraphResp>(GET_PIPELINE_RUN_GRAPH, {
    variables: { id: runId },
    fetchPolicy: "cache-and-network",
    pollInterval: 10000,
  });

  const run = data?.astroliftPipelineRun ?? null;

  const merged = new Map((run?.jobRuns ?? []).map((job) => [job.id, job]));
  for (const job of additional) if (!merged.has(job.id)) merged.set(job.id, job);
  const stages: PipelineDagStage[] = [...merged.values()].map((jr) => ({
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

  async function onLoadMore() {
    if (loadingMore) return;
    setLoadingMore(true);
    setPageError(false);
    try {
      const response = await client.query<{
        pipelineJobRunsPage: { items: JobRunNode[]; nextCursor: string | null };
      }>({
        query: GET_PIPELINE_JOB_PAGE,
        variables: { runId, after: pagesLoaded ? nextCursor : null },
        fetchPolicy: "no-cache",
      });
      const page = response.data?.pipelineJobRunsPage;
      if (!page) throw new Error("missing");
      setAdditional((jobs) => [...jobs, ...page.items]);
      setNextCursor(page.nextCursor);
      setPagesLoaded(true);
    } catch {
      setPageError(true);
    } finally {
      setLoadingMore(false);
    }
  }
  return {
    loading: initialLoading,
    stages,
    truncated: Boolean(run?.jobsTruncated && (!pagesLoaded || nextCursor)),
    loadingMore,
    pageError,
    onLoadMore,
  };
}

const GET_PIPELINE_JOB_PAGE = gql`
  query GetPipelineGraphJobsPage($runId: GUID!, $after: String) {
    pipelineJobRunsPage(runId: $runId, limit: 20, after: $after) {
      items {
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
      nextCursor
    }
  }
`;
