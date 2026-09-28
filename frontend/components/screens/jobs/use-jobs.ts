"use client";

import { gql } from "@apollo/client";
import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useListState, useLocalListState } from "@/components/list/use-list-state";
import { RUN_JOB_ONCE } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import {
  type CronWorkload,
  JOBS_LIST,
  type JobRunPulse,
  RECENT_RUNS,
  selectJobs,
  withLastRuns,
} from "./jobs-list";

/**
 * Every cron workload, fleet-wide or for one app. `kinds` narrows on the
 * server (the old page spent `search` on "cronjob" and dropped the false
 * positives), which leaves `search` free; the list searches client-side
 * anyway, over the whole walked set.
 */
const LIST_CRON_JOBS_PAGE = gql`
  query ListCronJobsPage($appSlug: String, $kinds: [String!], $limit: Int, $after: String) {
    astroliftWorkloadsPage(appSlug: $appSlug, kinds: $kinds, limit: $limit, after: $after) {
      items {
        id
        slug
        name
        kind
        schedule
        concurrencyPolicy
        registeredAppSlug
      }
      nextCursor
      totalCount
    }
  }
`;

/** The recent runs each job's Last run and the Failing view read. */
const RECENT_JOB_RUNS = gql`
  query RecentJobRuns($appSlug: String, $limit: Int) {
    astroliftScheduledJobRunsPage(appSlug: $appSlug, limit: $limit) {
      items {
        id
        status
        registeredAppSlug
        workloadSlug
        environmentName
        startedAt
        createdAt
      }
    }
  }
`;

interface CronJobsResp {
  astroliftWorkloadsPage: CursorPage<CronWorkload>;
}

interface RecentRunsResp {
  astroliftScheduledJobRunsPage: { items: JobRunPulse[] };
}

/** The workloads page's own maximum: one request covers most fleets. */
const WALK_LIMIT = 200;
const KINDS = ["cronjob"];
const NO_JOBS: CronWorkload[] = [];

/**
 * Every cron workload: the first page through `useQuery` (so its poll and
 * cache apply), the rest, past one page, walked by cursor. Nothing past the
 * first page is dropped.
 */
function useCronJobs(appSlug: string | null) {
  const client = useApolloClient();
  const head = useQuery<CronJobsResp>(LIST_CRON_JOBS_PAGE, {
    variables: { appSlug, kinds: KINDS, limit: WALK_LIMIT },
    fetchPolicy: "cache-and-network",
  });
  const first = (head.data ?? head.previousData)?.astroliftWorkloadsPage;
  const cursor = first?.nextCursor ?? null;
  const [tail, setTail] = React.useState<{ after: string; rows: CronWorkload[] } | null>(null);
  const [tailError, setTailError] = React.useState<Error | null>(null);

  React.useEffect(() => {
    if (!cursor) return;
    let cancelled = false;
    (async () => {
      const rows: CronWorkload[] = [];
      let after: string | null = cursor;
      while (after && !cancelled) {
        const res: { data?: CronJobsResp } = await client.query<CronJobsResp>({
          query: LIST_CRON_JOBS_PAGE,
          variables: { appSlug, kinds: KINDS, limit: WALK_LIMIT, after },
          fetchPolicy: "network-only",
        });
        const page = res.data?.astroliftWorkloadsPage;
        rows.push(...(page?.items ?? []));
        after = page?.nextCursor ?? null;
      }
      if (!cancelled) {
        setTail({ after: cursor, rows });
        setTailError(null);
      }
    })().catch((e: unknown) => {
      if (!cancelled) setTailError(e instanceof Error ? e : new Error(String(e)));
    });
    return () => {
      cancelled = true;
    };
  }, [client, cursor, appSlug]);

  const tailRows = cursor && tail?.after === cursor ? tail.rows : NO_JOBS;
  const jobs = React.useMemo(
    () =>
      // A workload with no schedule is not a scheduled job.
      [...(first?.items ?? []), ...tailRows].filter((w) => w.kind === "cronjob" && w.schedule),
    [first?.items, tailRows]
  );
  return {
    jobs,
    loading: head.loading && !first,
    stale: Boolean(cursor && tail?.after !== cursor),
    error: (!first && head.error) || tailError,
    refetch: head.refetch,
  };
}

/**
 * Scheduled jobs, fleet-wide at /jobs (URL list state) or one app's on its
 * Workloads tab (`appSlug`, in-memory list state: the tab's own
 * `?section=`/`?kind=` must survive a filter change). The data half of
 * JobsScreen: the walk, each job's last run, the environments Run now
 * offers, and the run-now mutation.
 */
export function useJobs(appSlug?: string) {
  const routed = useListState(JOBS_LIST);
  const local = useLocalListState(JOBS_LIST);
  const list = appSlug ? local : routed;
  const { state } = list;
  const scope = appSlug ?? null;

  const cron = useCronJobs(scope);
  const recent = useQuery<RecentRunsResp>(RECENT_JOB_RUNS, {
    variables: { appSlug: scope, limit: RECENT_RUNS },
    fetchPolicy: "cache-and-network",
    pollInterval: 15000,
  });
  const runs = recent.data?.astroliftScheduledJobRunsPage.items;
  const rows = React.useMemo(() => withLastRuns(cron.jobs, runs ?? []), [cron.jobs, runs]);
  const { rows: pageRows, totalCount } = selectJobs(rows, {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });

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
    refetchQueries: ["RecentJobRuns", "ListScheduledJobRunsPage"],
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
    rows: pageRows,
    totalCount,
    loading: cron.loading,
    stale: cron.stale || (recent.loading && !recent.data),
    error: cron.error ? { message: cron.error.message } : null,
    onRetry: () => {
      void cron.refetch();
      void recent.refetch();
    },
    environments,
    pendingJob,
    onRun,
  };
}
