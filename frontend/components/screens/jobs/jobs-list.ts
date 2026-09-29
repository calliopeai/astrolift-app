/**
 * Apps › Scheduled jobs (spec 44 §4.1, §4.4, §5.1, §5.5): three list
 * declarations and the pure steps behind them, plus the run view of one job
 * run or command run.
 *
 *   /jobs           the scheduled jobs (cron workloads): All · Mine · Failing · Paused
 *   /jobs/runs      every job run, or one job's (`?app=&workload=`): All · Mine · Failed
 *   /jobs/commands  one-off command runs: All · Mine
 *
 * The jobs list is small and stable, so the hook walks every cron workload
 * and `selectJobs` filters, sorts and pages it here (numbered). Failing
 * reads each job's latest run from the recent-runs window. The run lists
 * are cursor paged on the server; status (job runs) and Mine (commands)
 * have no argument yet, so they narrow a wider page, as Deployments does.
 */
import type { SortState } from "@/components/data-table";
import { commandRunStatus, type RunStatus } from "@/components/jobs/RunStatusBadge";
import type { PanelFailure } from "@/components/panel/Panel";
import type { LogLine } from "@/components/run/LogView";
import type { TimelineStep } from "@/components/run/Timeline";
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import { spanMs } from "@/components/screens/deployments/run-support";
import type {
  AstroliftCommandRun,
  AstroliftScheduledJobRun,
} from "@/graphql/lifecycle/lifecycle.types";

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

/** A recent run, enough to say how a job last went. */
export interface JobRunPulse {
  id: string;
  status: string;
  registeredAppSlug: string;
  workloadSlug: string;
  environmentName: string;
  startedAt: string | null;
  createdAt: string;
}

export interface JobRow extends CronWorkload {
  /** The job's newest run in the recent window; null when none ran in it. */
  lastRun: JobRunPulse | null;
}

/** How many recent runs Failing and Last run read. */
export const RECENT_RUNS = 100;

const LAST_RUN = ["succeeded", "failed", "running", "never"];
const CONCURRENCY = ["forbid", "queue", "replace"];

export const JOBS_LIST: ListDefinition = {
  id: "apps.jobs",
  fields: [
    { key: "app", label: "App" },
    { key: "lastRun", label: "Last run", options: LAST_RUN.map((v) => ({ value: v, label: v })) },
    {
      key: "concurrency",
      label: "Concurrency",
      options: CONCURRENCY.map((v) => ({ value: v, label: v })),
    },
  ],
  searchPlaceholder: "Search jobs, apps, schedules…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews(
    { owner: "me" },
    [
      {
        key: "failing",
        label: "Failing",
        filters: { lastRun: "failed" },
        note: `Failing means the job's latest run failed, among the last ${RECENT_RUNS} runs.`,
      },
      {
        key: "paused",
        label: "Paused",
        filters: { paused: "yes" },
        note: "Astrolift does not record a paused schedule yet, so no job shows here.",
      },
    ],
    { mineNote: "Scheduled jobs do not record who declared them yet, so Mine is empty." }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

function jobKey(app: string, workload: string) {
  return `${app}/${workload}`;
}

/** Each job's newest run in `runs` (which arrive newest first). */
export function withLastRuns(jobs: CronWorkload[], runs: JobRunPulse[]): JobRow[] {
  const latest = new Map<string, JobRunPulse>();
  for (const r of runs) {
    const key = jobKey(r.registeredAppSlug, r.workloadSlug);
    const seen = latest.get(key);
    if (!seen || runTime(r) > runTime(seen)) latest.set(key, r);
  }
  return jobs.map((j) => ({
    ...j,
    lastRun: latest.get(jobKey(j.registeredAppSlug, j.slug)) ?? null,
  }));
}

function runTime(r: Pick<JobRunPulse, "startedAt" | "createdAt">): number {
  return Date.parse(r.startedAt ?? r.createdAt) || 0;
}

const JOB_SORT: Record<string, (j: JobRow) => string | number> = {
  name: (j) => j.slug.toLowerCase(),
  app: (j) => j.registeredAppSlug.toLowerCase(),
  schedule: (j) => j.schedule,
  lastRun: (j) => (j.lastRun ? runTime(j.lastRun) : 0),
};

function matchesJob(j: JobRow, filters: Record<string, string>, q: string): boolean {
  // Mine and Paused have no data behind them yet: they hold nothing, never everything.
  if (filters.owner || filters.paused) return false;
  if (filters.app && j.registeredAppSlug !== filters.app) return false;
  if (filters.lastRun && (j.lastRun?.status ?? "never") !== filters.lastRun) return false;
  if (filters.concurrency && j.concurrencyPolicy.toLowerCase() !== filters.concurrency)
    return false;
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return [j.slug, j.name, j.registeredAppSlug, j.schedule].some((f) =>
    f.toLowerCase().includes(needle)
  );
}

/** One numbered page of jobs: filtered, sorted, sliced; `totalCount` is the filtered count. */
export function selectJobs(
  jobs: JobRow[],
  {
    filters,
    q,
    sort,
    page,
    pageSize,
  }: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
): { rows: JobRow[]; totalCount: number } {
  const kept = jobs
    .filter((j) => matchesJob(j, filters, q))
    .sort((a, b) => {
      for (const s of sort) {
        const value = JOB_SORT[s.key];
        if (!value) continue;
        const x = value(a);
        const y = value(b);
        if (x < y) return s.dir === "asc" ? -1 : 1;
        if (x > y) return s.dir === "asc" ? 1 : -1;
      }
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
  const start = (Math.max(1, page) - 1) * pageSize;
  return { rows: kept.slice(start, start + pageSize), totalCount: kept.length };
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

/** The page walked when the status chip narrows it client-side. */
export const NARROW_LIMIT = 100;

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
  views: standardViews(
    { startedBy: "me" },
    [
      {
        key: "failed",
        label: "Failed",
        filters: { status: "failed" },
        note: `Failed keeps the failed runs among the newest ${NARROW_LIMIT}, until the runs query takes a status.`,
      },
    ],
    {
      mineNote:
        "Job runs are started by their schedule and do not record a person, so Mine is empty.",
    }
  ),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

export interface JobRunsVariables {
  appSlug: string | null;
  workloadSlug: string | null;
  environmentName: string | null;
  search: string | null;
  limit: number;
  after: string | null;
}

export function jobRunsVariables(
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
): JobRunsVariables {
  return {
    appSlug: filters.app || null,
    workloadSlug: filters.workload || null,
    environmentName: filters.environment || null,
    search: q.trim() || null,
    limit: filters.status ? Math.max(pageSize, NARROW_LIMIT) : pageSize,
    after,
  };
}

export function narrowJobRuns(
  rows: AstroliftScheduledJobRun[],
  filters: Record<string, string>
): AstroliftScheduledJobRun[] {
  if (filters.startedBy) return [];
  return filters.status ? rows.filter((r) => r.status === filters.status) : rows;
}

// ---------------------------------------------------------------------------
// Command runs
// ---------------------------------------------------------------------------

export const COMMAND_RUNS_LIST: ListDefinition = {
  id: "apps.jobs.commands",
  fields: [
    { key: "app", label: "App" },
    { key: "invokedBy", label: "Invoked by" },
  ],
  // The server matches app, workload, the operator who invoked it and the Job name.
  searchPlaceholder: "Search commands, apps, operators…",
  defaultSort: [{ key: "started", dir: "desc" }],
  views: standardViews({ invokedBy: "me" }, [], {
    mineNote: `Mine keeps the commands you invoked among the newest ${NARROW_LIMIT}, until the commands query takes who invoked them.`,
  }),
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
    limit: filters.invokedBy ? Math.max(pageSize, NARROW_LIMIT) : pageSize,
    after,
  };
}

/** `invokedBy: me` keeps the viewer's own; any other value matches the username. */
export function narrowCommandRuns(
  rows: AstroliftCommandRun[],
  filters: Record<string, string>,
  me: string | null
): AstroliftCommandRun[] {
  const by = filters.invokedBy;
  if (!by) return rows;
  if (by === "me") return me ? rows.filter((r) => r.invokedByUsername === me) : [];
  return rows.filter((r) => (r.invokedByUsername ?? "").toLowerCase().includes(by.toLowerCase()));
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
