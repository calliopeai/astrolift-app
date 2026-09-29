"use client";

import { gql } from "@apollo/client";
import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { useListState, useLocalListState } from "@/components/list/use-list-state";
import { RUN_JOB_ONCE } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import { type CronWorkload, JOBS_LIST, type JobRow, jobsVariables, narrowJobs } from "./jobs-list";

/**
 * One numbered page of cron workloads, fleet-wide or for one app, each with
 * its latest run: `kinds` holds the page to cron jobs, and the §5.1
 * arguments filter, sort and count on the server (#2155).
 */
const LIST_CRON_JOBS_PAGE = gql`
  query ListCronJobsPage(
    $appSlug: String
    $kinds: [String!]
    $search: String
    $filter: AstroliftWorkloadsFilter
    $sort: String
    $page: Int
    $pageSize: Int
  ) {
    astroliftWorkloadsPage(
      appSlug: $appSlug
      kinds: $kinds
      search: $search
      filter: $filter
      sort: $sort
      page: $page
      pageSize: $pageSize
    ) {
      items {
        id
        slug
        name
        kind
        schedule
        concurrencyPolicy
        registeredAppSlug
        lastRun {
          id
          status
          startedAt
          createdAt
        }
      }
      totalCount
      page
      pageSize
    }
  }
`;

interface CronJobsResp {
  astroliftWorkloadsPage: { items: JobRow[]; totalCount: number | null };
}

/**
 * Scheduled jobs, fleet-wide at /jobs (URL list state) or one app's on its
 * Workloads tab (`appSlug`, in-memory list state: the tab's own
 * `?section=`/`?kind=` must survive a filter change). The data half of
 * JobsScreen: the page, the environments Run now offers, and the run-now
 * mutation.
 */
export function useJobs(appSlug?: string) {
  const routed = useListState(JOBS_LIST);
  const local = useLocalListState(JOBS_LIST);
  const list = appSlug ? local : routed;
  const { state } = list;
  const scope = appSlug ?? null;

  const query = useQuery<CronJobsResp>(LIST_CRON_JOBS_PAGE, {
    variables: jobsVariables(scope, {
      q: state.q,
      filters: list.filters,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    }),
    fetchPolicy: "cache-and-network",
    // Each row's last run settles while an operator watches.
    pollInterval: 15000,
  });
  const data = query.data ?? query.previousData;
  const page = data?.astroliftWorkloadsPage;
  const rows = narrowJobs(page?.items ?? [], list.filters);

  const envs = useQuery<{ astroliftEnvironments: AstroliftAppEnvironment[] }>(LIST_ENVIRONMENTS, {
    variables: { appSlug: scope },
    fetchPolicy: "cache-and-network",
  });
  const environments = envs.data?.astroliftEnvironments ?? [];
  const [pendingJob, setPendingJob] = React.useState<string | null>(null);

  const [runOnce] = useMutation<{
    runAstroliftJobOnce: {
      ok: boolean;
      errors: { code: string; message: string }[];
      data: { runName: string; namespace: string; logsUrl: string } | null;
    };
  }>(RUN_JOB_ONCE, {
    // By operation name: the run lists carry cursors and filters, so no
    // literal variables object names the page an operator returns to.
    refetchQueries: ["ListCronJobsPage", "ListScheduledJobRunsPage"],
  });

  async function onRun(job: CronWorkload, environmentName: string) {
    const key = `${job.registeredAppSlug}/${job.slug}`;
    setPendingJob(key);
    try {
      const { data } = await runOnce({
        variables: {
          input: { appSlug: job.registeredAppSlug, environmentName, jobSlug: job.slug },
        },
      });
      const res = data?.runAstroliftJobOnce;
      if (res?.ok) {
        toast.success(
          `${job.slug} dispatched as ${res.data?.runName ?? "manual run"} (${environmentName})`
        );
      } else {
        toast.error(res?.errors?.[0]?.message ?? "Run failed");
      }
    } finally {
      setPendingJob(null);
    }
  }

  return {
    list,
    embedded: Boolean(appSlug),
    appSlug: appSlug ?? null,
    rows,
    // The server's count of jobs: Failing narrows each page, and its note says so.
    totalCount: page?.totalCount ?? rows.length,
    loading: query.loading && !data,
    // Rows on screen answer the previous list state while the next loads.
    stale: query.loading && !query.data && Boolean(data),
    error: query.error && !data ? { message: query.error.message } : null,
    onRetry: () => {
      void query.refetch();
    },
    environments,
    pendingJob,
    onRun,
  };
}
