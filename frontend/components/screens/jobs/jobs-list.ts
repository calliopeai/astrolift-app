/**
 * Apps › Scheduled jobs (spec 44 §4.1, §4.4, §5.1, §5.5): three list
 * declarations and the pure steps behind them, plus the run view of one job
 * run or command run.
 *
 *   /jobs           the scheduled jobs (cron workloads): All · Mine · Failing · Paused
 *   /jobs/runs      every job run, or one job's (`?app=&workload=`): All · Mine · Failed
 *   /jobs/commands  one-off command runs: All · Mine
 *
 * The jobs list is one numbered page of `astroliftWorkloadsPage` held to
 * the cronjob kind: app, Mine (the owner), search, sort and the page go to
 * the server, and each row carries its latest run. Failing is the one view
 * the server cannot answer yet (the workloads filter has no last-run
 * status), so it keeps the failing jobs of each page and says so. The run
 * lists are cursor paged on the server, their status, trigger and Mine
 * through each query's `filter` (#2155).
 */
import type { SortState } from "@/components/data-table";
import { commandRunStatus, type RunStatus } from "@/components/jobs/RunStatusBadge";
import type { PanelFailure } from "@/components/panel/Panel";
import type { LogLine } from "@/components/run/LogView";
import type { TimelineStep } from "@/components/run/Timeline";
import { formatSort, type ListDefinition, standardViews } from "@/components/list/list-state";
import { spanMs } from "@/components/screens/deployments/run-support";
import type { AstroliftCommandRun } from "@/graphql/lifecycle/lifecycle.types";

// ---------------------------------------------------------------------------
// Scheduled jobs
// ---------------------------------------------------------------------------

export interface CronWorkload {
  id: string;
  slug: string;
  name: string;
  kind: string;
  schedule: string;
  concurrencyPolicy: string;
  registeredAppSlug: string;
}

/** A job's latest run, enough to say how it last went (the workload's `lastRun`). */
export interface JobRunPulse {
  id: string;
  status: string;
  startedAt: string | null;
  createdAt: string;
}

export interface JobRow extends CronWorkload {
  /** The job's latest run; null when it never ran. */
  lastRun: JobRunPulse | null;
}

export const JOBS_LIST: ListDefinition = {
  id: "apps.jobs",
  fields: [{ key: "app", label: "App" }],
  searchPlaceholder: "Search jobs, apps…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews({ owner: "me" }, [
    {
      key: "failing",
      label: "Failing",
      filters: { lastRun: "failed" },
      note: "Failing keeps the jobs on each page whose latest run failed, until the jobs query can filter on the last run.",
    },
    {
      key: "paused",
      label: "Paused",
      filters: { paused: "yes" },
      note: "Astrolift does not record a paused schedule yet, so no job shows here.",
    },
  ]),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

const CRON_KINDS = ["cronjob"];

/** What the jobs page query is sent for a list state, fleet-wide or one app's. */
export function jobsVariables(
  appSlug: string | null,
  {
    q,
    filters,
    sort,
    page,
    pageSize,
  }: {
    q: string;
    filters: Record<string, string>;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
) {
  const filter: { app?: string[]; owner?: string[] } = {};
  if (filters.app) filter.app = [filters.app];
  if (filters.owner) filter.owner = [filters.owner];
  return {
    appSlug,
    kinds: CRON_KINDS,
    search: q.trim() || null,
    filter: Object.keys(filter).length > 0 ? filter : null,
    sort: formatSort(sort),
    page: Math.max(1, page),
    pageSize,
  };
}

/**
 * What the server cannot answer, over the page in hand: Failing keeps the
 * jobs whose latest run failed; Paused has no data behind it yet, so it
 * holds nothing, never everything.
 */
export function narrowJobs(rows: JobRow[], filters: Record<string, string>): JobRow[] {
  if (filters.paused) return [];
  if (filters.lastRun) return rows.filter((j) => j.lastRun?.status === filters.lastRun);
  return rows;
}

/** The job's runs list. */
export function jobRunsHref(app: string, workload?: string): string {
  const p = new URLSearchParams({ app });
  if (workload) p.set("workload", workload);
  return `/jobs/runs?${p.toString()}`;
}

// ---------------------------------------------------------------------------
// Job runs
// ---------------------------------------------------------------------------

const RUN_STATUSES = ["running", "succeeded", "failed", "superseded"];

export const JOB_RUNS_LIST: ListDefinition = {
  id: "apps.jobs.runs",
  fields: [
    { key: "app", label: "App" },
    { key: "workload", label: "Job" },
    { key: "environment", label: "Environment" },
    { key: "status", label: "Status", options: RUN_STATUSES.map((v) => ({ value: v, label: v })) },
  ],
  // The server matches app, workload, environment, status and the batch/v1 Job name.
  searchPlaceholder: "Search runs, jobs, Job names…",
  defaultSort: [{ key: "started", dir: "desc" }],
  // Mine: the runs you started with Run now; the schedule starts the rest.
  views: standardViews({ startedBy: "me" }, [
    { key: "failed", label: "Failed", filters: { status: "failed" } },
  ]),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

export interface JobRunsVariables {
  appSlug: string | null;
  workloadSlug: string | null;
  environmentName: string | null;
  search: string | null;
  filter: { status?: string[]; triggeredBy?: string[] } | null;
  limit: number;
  after: string | null;
}

export function jobRunsVariables(
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
): JobRunsVariables {
  const filter: { status?: string[]; triggeredBy?: string[] } = {};
  if (filters.status) filter.status = [filters.status];
  if (filters.startedBy) filter.triggeredBy = [filters.startedBy];
  return {
    appSlug: filters.app || null,
    workloadSlug: filters.workload || null,
    environmentName: filters.environment || null,
    search: q.trim() || null,
    filter: Object.keys(filter).length > 0 ? filter : null,
    limit: pageSize,
    after,
  };
}

// ---------------------------------------------------------------------------
// Command runs
// ---------------------------------------------------------------------------

export const COMMAND_RUNS_LIST: ListDefinition = {
  id: "apps.jobs.commands",
  fields: [{ key: "app", label: "App" }],
  // The server matches app, workload, the operator who invoked it and the Job name.
  searchPlaceholder: "Search commands, apps, operators…",
  defaultSort: [{ key: "started", dir: "desc" }],
  views: standardViews({ invokedBy: "me" }),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

export function commandRunsVariables(
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
) {
  return {
    appSlug: filters.app || null,
    search: q.trim() || null,
    filter: filters.invokedBy ? { invokedBy: [filters.invokedBy] } : null,
    limit: pageSize,
    after,
  };
}

export function commandText(command: unknown): string {
  return Array.isArray(command) ? command.join(" ") : String(command ?? "");
}

// ---------------------------------------------------------------------------
// One run on the run archetype (spec 44 §5.5)
// ---------------------------------------------------------------------------

interface RunTimes {
  createdAt: string;
  startedAt?: string | null;
  endedAt?: string | null;
}

/**
 * A run's two phases: waiting for a pod (created → started) and the run
 * itself (started → ended). `name` is what ran: the Job or the command.
 */
export function runSteps(
  run: RunTimes,
  status: RunStatus,
  name: string,
  now: number,
  detail?: string
): TimelineStep[] {
  const started = Boolean(run.startedAt);
  const live = status === "running" || status === "in_progress";
  return [
    {
      id: "schedule",
      name: "scheduled",
      state: started ? "ok" : live ? "running" : status === "failed" ? "failed" : "pending",
      durationMs: spanMs(run.createdAt, run.startedAt ?? (live ? now : null)),
    },
    {
      id: "run",
      name,
      state: !started
        ? "pending"
        : live
          ? "running"
          : status === "failed"
            ? "failed"
            : status === "superseded"
              ? "skipped"
              : "ok",
      durationMs: spanMs(run.startedAt, run.endedAt ?? (live && started ? now : null)),
      detail,
    },
  ];
}

/** Elapsed while live, the total once ended. */
export function runElapsed(run: RunTimes, live: boolean, now: number): number | null {
  const from = run.startedAt ?? (live ? run.createdAt : null);
  return spanMs(from, run.endedAt ?? (live ? now : null));
}

/**
 * The run's captured output as log lines. Output carries no per-line time,
 * so every line takes the moment the run started (or was created).
 */
export function outputLines(output: string, at: string): LogLine[] {
  if (!output) return [];
  return output
    .replace(/\n$/, "")
    .split("\n")
    .map((message) => ({ ts: at, message }));
}

/** A failed run's reason: its exit code and the last line it wrote. */
export function runFailure(
  status: RunStatus,
  exitCode: number | null | undefined,
  output: string,
  excerpt: string
): PanelFailure | null {
  if (status !== "failed") return null;
  const last = (excerpt || output)
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .at(-1);
  const code = exitCode != null ? `exit ${exitCode}` : "failed";
  return { title: "Run failed", reason: last ? `${code}: ${last}` : code };
}

export function jobRunStatus(run: { status: string }): RunStatus {
  return (
    (["running", "succeeded", "failed", "superseded"] as const).find((s) => s === run.status) ??
    "unknown"
  );
}

export function commandStatus(run: Pick<AstroliftCommandRun, "endedAt" | "exitCode">): RunStatus {
  return commandRunStatus({ endedAt: run.endedAt, exitCode: run.exitCode });
}
