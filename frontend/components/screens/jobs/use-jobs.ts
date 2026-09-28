"use client";

import { gql } from "@apollo/client";
import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import {
  useCursorTable,
  type CursorPage,
  type CursorTableController,
} from "@/components/data-table";
import { RUN_JOB_ONCE } from "@/graphql/lifecycle/lifecycle.mutations";
import {
  LIST_COMMAND_RUNS_PAGE,
  LIST_ENVIRONMENTS,
  LIST_SCHEDULED_JOB_RUNS_PAGE,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftCommandRun,
  AstroliftScheduledJobRun,
} from "@/graphql/lifecycle/lifecycle.types";
import { LIST_WORKLOADS_PAGE } from "@/graphql/registry/registry.queries";

// ── page envelopes ────────────────────────────────────────────────────────

interface JobRunsPageResp {
  astroliftScheduledJobRunsPage: CursorPage<AstroliftScheduledJobRun>;
}

interface CommandRunsPageResp {
  astroliftCommandRunsPage: CursorPage<AstroliftCommandRun>;
}

export interface CronWorkload {
  id: string;
  slug: string;
  name: string;
  kind: string;
  schedule: string;
  concurrencyPolicy: string;
}

interface WorkloadsPageResp {
  astroliftWorkloadsPage: CursorPage<CronWorkload>;
}

/**
 * `astroliftScheduledJobRunsPage` has no `status:` argument and
 * `astroliftWorkloadsPage` has no `kind:` argument, so the two tabs that
 * are *defined* by one of those axes — Failures and Schedules — spend the
 * page field's `search` on it. `search` is an OR of `icontains` across the
 * run's app / workload / environment / status / Job name (and the
 * workload's name / slug / kind / app slug), so it narrows to a superset
 * server-side; the row guards below drop whatever matched on the wrong
 * column. Both tabs therefore render without a search box: `search` is
 * already carrying the filter.
 */
const FAILED_TERM = "failed";
const CRONJOB_TERM = "cronjob";

/**
 * Run volume for the 14-day sparkline above the Runs table, sized to the
 * window the summary draws rather than to a page of the table. This is an
 * aggregate, not a list — the table below it walks the same field with a
 * cursor.
 */
const SUMMARY_LIMIT = 100;

/**
 * Tab badge counts + the Runs summary in one round trip.
 *
 * Each badge wants the size of its tab, not the rows in it, so the count
 * aliases ask for `limit: 1` and read `totalCount`. These numbers used to
 * be `.length` on the capped fetch that also drew the table, so they
 * stopped counting at the cap (100 runs, 200 workloads) and said so with
 * no hint that they had.
 */
const JOBS_OVERVIEW = gql`
  query JobsOverview(
    $appSlug: String
    $failedTerm: String
    $cronjobTerm: String
    $summaryLimit: Int
  ) {
    runs: astroliftScheduledJobRunsPage(appSlug: $appSlug, limit: $summaryLimit) {
      totalCount
      items {
        id
        status
        startedAt
        createdAt
      }
    }
    failures: astroliftScheduledJobRunsPage(appSlug: $appSlug, search: $failedTerm, limit: 1) {
      totalCount
    }
    commands: astroliftCommandRunsPage(appSlug: $appSlug, limit: 1) {
      totalCount
    }
    schedules: astroliftWorkloadsPage(appSlug: $appSlug, search: $cronjobTerm, limit: 1) {
      totalCount
    }
  }
`;

export interface JobRunPulse {
  id: string;
  status: string;
  startedAt: string | null;
  createdAt: string;
}

interface JobsOverviewResp {
  runs: { totalCount: number | null; items: JobRunPulse[] } | null;
  failures: { totalCount: number | null } | null;
  commands: { totalCount: number | null } | null;
  schedules: { totalCount: number | null } | null;
}

export type JobsTab = "schedules" | "runs" | "failures" | "commands" | "logs";

/**
 * Narrow a controller to the rows that survive a client-side guard.
 *
 * `CursorTableController<T>` mentions `T` only in `rows`, so the walk —
 * cursor, page size, search, error and retry — is untouched: this is not
 * client-side pagination, it is dropping the false positives a `search`-
 * shaped server filter cannot exclude. `state` has to be recomputed or a
 * page whose rows all fail the guard renders as nothing at all, which is
 * exactly the failure mode DataTable's required `empty` exists to kill.
 */
function guardRows<TRow>(
  controller: CursorTableController<TRow>,
  rows: TRow[]
): CursorTableController<TRow> {
  return {
    ...controller,
    rows,
    state:
      controller.state === "ready" && rows.length === 0
        ? controller.isFiltered
          ? "emptyFiltered"
          : "empty"
        : controller.state,
  };
}

/**
 * The data half of JobsScreen: the active tab (it decides which table
 * walks), the overview query behind the tab badges and the runs summary,
 * and one cursor controller per tab.
 */
export function useJobs(appSlug?: string) {
  const [tab, setTab] = React.useState<JobsTab>("schedules");

  const appVariables = React.useMemo(() => ({ appSlug: appSlug ?? null }), [appSlug]);

  const overview = useQuery<JobsOverviewResp>(JOBS_OVERVIEW, {
    variables: {
      ...appVariables,
      failedTerm: FAILED_TERM,
      cronjobTerm: CRONJOB_TERM,
      summaryLimit: SUMMARY_LIMIT,
    },
    fetchPolicy: "cache-and-network",
    pollInterval: 15000,
  });

  // One controller per tab. Each is skipped while its tab is hidden — the
  // badges come from the overview above, so an unseen tab costs nothing
  // and re-fetches on arrival (cache-and-network).
  const runsTable = useCursorTable<AstroliftScheduledJobRun>({
    query: LIST_SCHEDULED_JOB_RUNS_PAGE,
    variables: appVariables,
    extract: (d) => (d as JobRunsPageResp | undefined)?.astroliftScheduledJobRunsPage,
    searchVariable: "search",
    urlKey: "run",
    pollInterval: 15000,
    skip: tab !== "runs",
  });

  const failuresTable = useCursorTable<AstroliftScheduledJobRun>({
    query: LIST_SCHEDULED_JOB_RUNS_PAGE,
    variables: { ...appVariables, search: FAILED_TERM },
    extract: (d) => (d as JobRunsPageResp | undefined)?.astroliftScheduledJobRunsPage,
    urlKey: "fail",
    pollInterval: 15000,
    skip: tab !== "failures",
  });

  const commandsTable = useCursorTable<AstroliftCommandRun>({
    query: LIST_COMMAND_RUNS_PAGE,
    variables: appVariables,
    extract: (d) => (d as CommandRunsPageResp | undefined)?.astroliftCommandRunsPage,
    searchVariable: "search",
    urlKey: "cmd",
    pollInterval: 15000,
    skip: tab !== "commands",
  });

  // Fleet-wide /jobs has no single app to anchor a schedule list to, so
  // the tab renders a pointer at the per-app view instead of a table.
  const schedulesTable = useCursorTable<CronWorkload>({
    query: LIST_WORKLOADS_PAGE,
    variables: { ...appVariables, search: CRONJOB_TERM },
    extract: (d) => (d as WorkloadsPageResp | undefined)?.astroliftWorkloadsPage,
    urlKey: "sched",
    skip: !appSlug || tab !== "schedules",
  });

  const cronRows = React.useMemo(
    () => schedulesTable.rows.filter((w) => w.kind === "cronjob" && Boolean(w.schedule)),
    [schedulesTable.rows]
  );
  const schedulesController = guardRows(schedulesTable, cronRows);

  const failedRows = React.useMemo(
    () => failuresTable.rows.filter((j) => j.status === "failed"),
    [failuresTable.rows]
  );
  const failuresController = guardRows(failuresTable, failedRows);

  function onRan() {
    overview.refetch().catch(() => {
      // Swallowed: the badge counts are re-polled every 15s.
    });
  }

  return {
    tab,
    setTab,
    summaryRuns: overview.data?.runs?.items ?? [],
    scheduleCount: overview.data?.schedules?.totalCount ?? null,
    runCount: overview.data?.runs?.totalCount ?? null,
    failureCount: overview.data?.failures?.totalCount ?? null,
    commandCount: overview.data?.commands?.totalCount ?? null,
    runsTable,
    failuresController,
    commandsTable,
    schedulesController,
    onRan,
  };
}

/**
 * Environments + the run-now mutation behind the cron workloads table
 * (#670). Mounted only while the per-app Schedules tab is shown.
 */
export function useCronWorkloadRun(appSlug: string, onRan: () => void) {
  const envs = useQuery<{ astroliftEnvironments: AstroliftAppEnvironment[] }>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const envList = envs.data?.astroliftEnvironments ?? [];
  const [pendingSlug, setPendingSlug] = React.useState<string | null>(null);

  const [runOnce] = useMutation<{
    runAstroliftJobOnce: {
      ok: boolean;
      errors: { code: string; message: string }[];
      data: { runName: string; namespace: string; logsUrl: string } | null;
    };
  }>(RUN_JOB_ONCE, {
    // Refetch by operation name: the runs walk carries a cursor, a page
    // size and a search term, so no literal variables object names the
    // page an operator is actually looking at. "JobsOverview" is the
    // query that is always mounted here — the runs table lives on
    // another tab and re-fetches when the operator switches to it.
    refetchQueries: ["JobsOverview"],
  });

  async function onRun(jobSlug: string, envName: string) {
    if (!envName) {
      toast.error("Pick an environment first.");
      return;
    }
    setPendingSlug(jobSlug);
    try {
      const { data } = await runOnce({
        variables: { input: { appSlug, environmentName: envName, jobSlug } },
      });
      const res = data?.runAstroliftJobOnce;
      if (res?.ok) {
        toast.success(`${jobSlug} dispatched as ${res.data?.runName ?? "manual run"} (${envName})`);
        onRan();
      } else {
        toast.error(res?.errors?.[0]?.message ?? "Run failed");
      }
    } finally {
      setPendingSlug(null);
    }
  }

  return { envList, pendingSlug, onRun };
}
