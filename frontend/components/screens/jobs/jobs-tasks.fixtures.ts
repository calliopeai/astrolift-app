import { fakeController } from "@/components/data-table/fixtures";
import type {
  AstroliftAppEnvironment,
  AstroliftCommandRun,
  AstroliftScheduledJobRun,
  AstroliftTaskRun,
} from "@/graphql/lifecycle/lifecycle.types";

import type { TaskWorkload } from "../tasks/use-tasks";

import type { CommandRunDetailProps } from "./CommandRunDetail";
import type { JobRunDetailProps } from "./JobRunDetail";
import type { CronWorkloadsTableProps, JobsScreenProps } from "./JobsScreen";
import type { CronWorkload, JobRunPulse } from "./use-jobs";

/** Hand-typed fixtures for the jobs and tasks screens. */

/** The generated JSON scalar is typed Record<string, unknown>; real values can be any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

const noop = () => {};

const LONG_APP = "customer-billing-reconciliation-service-with-an-unusually-long-slug";
const LONG_WORKLOAD = "nightly-ledger-export-to-the-data-warehouse-and-cold-storage-archive";

// ── jobs ──────────────────────────────────────────────────────────────────

export function jobRun(
  id: string,
  patch: Partial<AstroliftScheduledJobRun> = {}
): AstroliftScheduledJobRun {
  return {
    id,
    registeredAppSlug: "billing",
    environmentName: "production",
    workloadSlug: "nightly-report",
    k8sJobName: `nightly-report-${id.slice(0, 5)}`,
    status: "succeeded",
    exitCode: 0,
    durationSeconds: 184,
    output:
      "Generating report for 2026-09-27…\nWrote 1,204 rows to s3://reports/2026-09-27.csv\nDone.",
    logExcerpt: "Done.",
    createdAt: "2026-09-28T02:00:00Z",
    startedAt: "2026-09-28T02:00:04Z",
    endedAt: "2026-09-28T02:03:08Z",
    ...patch,
  };
}

export const JOB_RUNS: AstroliftScheduledJobRun[] = [
  jobRun("5f2a1c90-8d4e-4b7a-9a31-0c7e2b6d1f01", {
    status: "running",
    exitCode: null,
    durationSeconds: null,
    endedAt: null,
  }),
  jobRun("4e1b0d8f-7c3d-4a69-8920-1b6d1a5c0e02"),
  jobRun("3d0a9c7e-6b2c-4958-9810-2a5c0b4bfd03", {
    status: "failed",
    exitCode: 1,
    durationSeconds: 12,
    workloadSlug: "sync-invoices",
    k8sJobName: "sync-invoices-28f1a",
  }),
  jobRun("2c9f8b6d-5a1b-4847-8700-3b4bfa3aec04", {
    environmentName: "preview-pr-412",
    durationSeconds: 45,
  }),
];

export const FAILED_RUNS = JOB_RUNS.filter((r) => r.status === "failed");

export function commandRun(
  id: string,
  patch: Partial<AstroliftCommandRun> = {}
): AstroliftCommandRun {
  return {
    id,
    registeredAppSlug: "billing",
    workloadSlug: "web",
    command: json(["python", "manage.py", "migrate"]),
    invokedByUsername: "leo",
    exitCode: 0,
    output:
      "Operations to perform:\n  Apply all migrations: billing\nRunning migrations:\n  No migrations to apply.",
    logExcerpt: "No migrations to apply.",
    createdAt: "2026-09-28T09:12:00Z",
    startedAt: "2026-09-28T09:12:02Z",
    endedAt: "2026-09-28T09:12:09Z",
    ...patch,
  };
}

export const COMMAND_RUNS: AstroliftCommandRun[] = [
  commandRun("9a8b7c6d-1111-4a2b-8c3d-4e5f6a7b8c01", { exitCode: null, endedAt: null, output: "" }),
  commandRun("8b7c6d5e-2222-4b3c-9d4e-5f6a7b8c9d02"),
  commandRun("7c6d5e4f-3333-4c4d-8e5f-6a7b8c9d0e03", {
    command: json(["sh", "-c", "exit 2"]),
    exitCode: 2,
    workloadSlug: null,
    invokedByUsername: null,
  }),
];

export function cronWorkload(slug: string, patch: Partial<CronWorkload> = {}): CronWorkload {
  return {
    id: `wl-${slug}`,
    slug,
    name: slug,
    kind: "cronjob",
    schedule: "0 2 * * *",
    concurrencyPolicy: "Forbid",
    ...patch,
  };
}

export const CRON_WORKLOADS: CronWorkload[] = [
  cronWorkload("nightly-report"),
  cronWorkload("sync-invoices", { schedule: "*/15 * * * *", concurrencyPolicy: "Allow" }),
  cronWorkload("weekly-digest", { schedule: "0 9 * * 1", concurrencyPolicy: "Replace" }),
];

function environment(name: string): AstroliftAppEnvironment {
  return {
    id: `env-${name}`,
    name,
    registeredAppSlug: "billing",
    createdAt: "2026-09-01T12:00:00Z",
    deploysPaused: false,
    ingressPaused: false,
    requiredApprovals: 0,
    settings: [],
    url: `https://${name}.billing.example.com`,
    clusterId: null,
    clusterProviderPluginSlug: null,
    clusterSlug: null,
    domainZone: null,
  };
}

export const ENVIRONMENTS: AstroliftAppEnvironment[] = [
  environment("production"),
  environment("staging"),
];

/** Pulse rows for the 14-day summary, spread back from the render time. */
function pulses(): JobRunPulse[] {
  const now = Date.now();
  const statuses = ["succeeded", "succeeded", "failed", "succeeded", "running"];
  return Array.from({ length: 30 }, (_, i) => {
    const at = new Date(now - (i % 14) * 86_400_000 - i * 60_000).toISOString();
    return { id: `p-${i}`, status: statuses[i % statuses.length], startedAt: at, createdAt: at };
  });
}

export const JOBS: JobsScreenProps = {
  appSlug: "billing",
  tab: "schedules",
  setTab: noop,
  summaryRuns: pulses(),
  scheduleCount: CRON_WORKLOADS.length,
  runCount: 128,
  failureCount: 3,
  commandCount: 17,
  runsTable: fakeController({ rows: JOB_RUNS, totalCount: 128, hasNext: true }),
  failuresController: fakeController({ rows: FAILED_RUNS, totalCount: 3, searchEnabled: false }),
  commandsTable: fakeController({ rows: COMMAND_RUNS, totalCount: 17 }),
  schedulesController: fakeController({ rows: CRON_WORKLOADS, searchEnabled: false }),
  onRan: noop,
};

export const CRON_TABLE: CronWorkloadsTableProps = {
  appSlug: "billing",
  controller: JOBS.schedulesController,
  envList: ENVIRONMENTS,
  pendingSlug: null,
  onRun: async () => {},
};

export const LONG_JOB_RUNS: AstroliftScheduledJobRun[] = [
  jobRun("1b8e7a5c-4f0a-4736-8600-4c3af92adb05", {
    registeredAppSlug: LONG_APP,
    workloadSlug: LONG_WORKLOAD,
    environmentName: "preview-feature-branch-with-a-very-long-descriptive-name",
    k8sJobName: `${LONG_WORKLOAD}-manual-28f1a9c3`,
    durationSeconds: 7384,
    output: Array.from({ length: 40 }, (_, i) => `[${i}] ${"exporting partition ".repeat(8)}`).join(
      "\n"
    ),
  }),
];

export const LONG_COMMAND_RUNS: AstroliftCommandRun[] = [
  commandRun("6d5e4f3a-4444-4d5e-9f6a-7b8c9d0e1f04", {
    registeredAppSlug: LONG_APP,
    workloadSlug: LONG_WORKLOAD,
    command: json([
      "python",
      "-m",
      "billing.maintenance.reconcile_ledger_entries",
      "--from=2026-01-01",
      "--to=2026-09-28",
      "--dry-run",
      "--verbose",
    ]),
    invokedByUsername: "an.operator.with.a.long.username@example.com",
  }),
];

export const JOB_RUN_DETAIL: JobRunDetailProps = {
  id: JOB_RUNS[1].id,
  loading: false,
  run: JOB_RUNS[1],
};

export const COMMAND_RUN_DETAIL: CommandRunDetailProps = {
  id: COMMAND_RUNS[1].id,
  loading: false,
  run: COMMAND_RUNS[1],
};

// ── tasks ─────────────────────────────────────────────────────────────────

export function taskWorkload(slug: string, patch: Partial<TaskWorkload> = {}): TaskWorkload {
  return {
    id: `wl-${slug}`,
    slug,
    name: slug.replace(/-/g, " "),
    kind: "task",
    registeredAppSlug: "billing",
    ...patch,
  };
}

export const TASK_WORKLOADS: TaskWorkload[] = [
  taskWorkload("db-migrate", { name: "Database migrations" }),
  taskWorkload("seed-demo-data", { name: "Seed demo data" }),
  taskWorkload("export-ledger", { name: "Export ledger", registeredAppSlug: "finance" }),
];

export function taskRun(id: string, patch: Partial<AstroliftTaskRun> = {}): AstroliftTaskRun {
  return {
    id,
    registeredAppSlug: "billing",
    workloadSlug: "db-migrate",
    triggerKind: "manual",
    triggeredByUsername: "leo",
    command: ["python", "manage.py", "migrate"],
    status: "succeeded",
    exitCode: 0,
    startedAt: "2026-09-28T09:30:02Z",
    endedAt: "2026-09-28T09:30:40Z",
    durationSeconds: 38,
    k8sJobName: `db-migrate-${id.slice(0, 5)}`,
    createdAt: "2026-09-28T09:30:00Z",
    ...patch,
  };
}

export const TASK_RUNS: AstroliftTaskRun[] = [
  taskRun("a1b2c3d4-0001-4e5f-8a9b-0c1d2e3f4a01", {
    status: "running",
    exitCode: null,
    endedAt: null,
    durationSeconds: null,
  }),
  taskRun("b2c3d4e5-0002-4f6a-9b0c-1d2e3f4a5b02"),
  taskRun("c3d4e5f6-0003-4a7b-8c1d-2e3f4a5b6c03", {
    workloadSlug: "seed-demo-data",
    status: "failed",
    exitCode: 137,
    durationSeconds: 305,
    triggerKind: "api",
    triggeredByUsername: null,
  }),
  taskRun("d4e5f6a7-0004-4b8c-9d2e-3f4a5b6c7d04", {
    status: "pending",
    exitCode: null,
    startedAt: null,
    endedAt: null,
    durationSeconds: null,
    triggerKind: "workflow",
    triggeredByUsername: null,
  }),
];

export const LONG_TASK_WORKLOADS: TaskWorkload[] = [
  taskWorkload(LONG_WORKLOAD, {
    name: "Nightly ledger export to the data warehouse and the cold storage archive",
    registeredAppSlug: LONG_APP,
  }),
];

export const LONG_TASK_RUNS: AstroliftTaskRun[] = [
  taskRun("e5f6a7b8-0005-4c9d-8e3f-4a5b6c7d8e05", {
    registeredAppSlug: LONG_APP,
    workloadSlug: LONG_WORKLOAD,
    command: [
      "python",
      "-m",
      "billing.maintenance.reconcile_ledger_entries",
      "--from=2026-01-01",
      "--to=2026-09-28",
    ],
    triggeredByUsername: "an.operator.with.a.long.username@example.com",
    k8sJobName: `${LONG_WORKLOAD}-28f1a9c3`,
    durationSeconds: 7384,
  }),
];
