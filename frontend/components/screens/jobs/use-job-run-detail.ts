"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import * as React from "react";

import { downloadText, logText, useNow } from "@/components/screens/deployments/run-support";
import {
  GET_SCHEDULED_JOB_RUN,
  LIST_SCHEDULED_JOB_RUNS,
} from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftScheduledJobRun } from "@/graphql/lifecycle/lifecycle.types";

import { outputLines } from "./jobs-list";

interface JobRunResp {
  astroliftScheduledJobRun: AstroliftScheduledJobRun | null;
}

interface JobRunListResp {
  astroliftScheduledJobRuns: AstroliftScheduledJobRun[];
}

/**
 * Scheduled job run by id (#1106, #1118). Prefers the singular
 * `astroliftScheduledJobRun(id)` query so a cold deep-link to a run outside the
 * 100-row list window still resolves. Falls back to the row in the cached
 * global LIST_SCHEDULED_JOB_RUNS window for an instant paint when navigated
 * from a runs list. Polls while the run is live, ticks its clock and builds
 * the log download. The data half of JobRunDetail.
 */
export function useJobRunDetail(id: string) {
  const client = useApolloClient();
  const { data, loading, error, refetch, startPolling, stopPolling } = useQuery<JobRunResp>(
    GET_SCHEDULED_JOB_RUN,
    {
      variables: { id },
      fetchPolicy: "cache-and-network",
    }
  );

  const cachedFromList = React.useMemo(() => {
    const listed = client.readQuery<JobRunListResp>({
      query: LIST_SCHEDULED_JOB_RUNS,
      variables: { limit: 100 },
    });
    return listed?.astroliftScheduledJobRuns.find((r) => r.id === id) ?? null;
  }, [client, id]);

  const run = data?.astroliftScheduledJobRun ?? cachedFromList;
  const running = run?.status === "running";
  React.useEffect(() => {
    if (running) startPolling(5000);
    else stopPolling();
  }, [running, startPolling, stopPolling]);
  const now = useNow(running);

  function onDownload() {
    if (!run) return;
    downloadText(
      `${run.k8sJobName || run.workloadSlug || run.id}.log`,
      logText(outputLines(run.output ?? "", run.startedAt ?? run.createdAt))
    );
  }

  return {
    loading,
    run,
    error: error && !run ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
    now,
    onDownload,
  };
}
