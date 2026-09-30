import type { GoldenSignalsPanelProps } from "@/components/observability/GoldenSignalsPanel";
import type {
  AstroliftAppGoldenSignal,
  AstroliftTimeSeriesPoint,
  GoldenSignalKind,
} from "@/graphql/__generated__/schema";
import type {
  AstroliftScheduledJobRun,
  AstroliftTaskRun,
} from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftWorkload, WorkloadKind } from "@/graphql/registry/registry.types";

import type { AppDoctorPanelViewProps } from "./AppDoctorPanel";
import type { BundleHomeScreenProps } from "./BundleHome";
import type { CronjobHomeScreenProps } from "./CronjobHome";
import type { FunctionHomeScreenProps } from "./FunctionHome";
import type { TaskHomeScreenProps } from "./TaskHome";

/** Hand-typed fixtures for the primitive app homes and the app doctor panel. */

const noop = () => {};

export const LONG =
  "billing-reconciliation-nightly-export-for-the-finance-warehouse-with-a-deliberately-long-name";

export function workload(overrides: Partial<AstroliftWorkload> = {}): AstroliftWorkload {
  return {
    version: 1,
    id: "wl-1",
    slug: "api",
    name: "API",
    kind: "deployment",
    registeredAppSlug: "billing",
    concurrencyPolicy: "",
    cpuLimit: "500m",
    cpuRequest: "100m",
    memoryLimit: "512Mi",
    memoryRequest: "128Mi",
    hpaMaxReplicas: null,
    hpaMinReplicas: null,
    hpaTargetCpuPct: 80,
    inClusterServiceFqdn: "api.billing.svc.cluster.local",
    isPublic: false,
    replicas: 2,
    schedule: "",
    storageClass: "",
    storageSize: "",
    ownedByMe: false,
    volumes: [] as unknown as AstroliftWorkload["volumes"],
    ...overrides,
  };
}

const kindWorkload = (kind: WorkloadKind, slug: string, name: string, extra = {}) =>
  workload({ id: `wl-${slug}`, kind, slug, name, ...extra });

// ── Bundle ─────────────────────────────────────────────────────────────────

export const BUNDLE: BundleHomeScreenProps = {
  name: "Billing",
  status: "ready",
  workloadHref: (slug) => `/apps/billing/workloads/${slug}`,
  workloads: [
    kindWorkload("deployment", "api", "API", { isPublic: true, replicas: 3 }),
    kindWorkload("statefulset", "ledger-db", "Ledger DB", { replicas: 1 }),
    kindWorkload("cronjob", "nightly-export", "Nightly export", { schedule: "0 2 * * *" }),
    kindWorkload("task", "migrate", "Migrate"),
    kindWorkload("function", "webhook", "Webhook", { isPublic: true }),
  ],
};

// ── Cronjob ────────────────────────────────────────────────────────────────

export const CRON_WORKLOAD = workload({
  kind: "cronjob",
  slug: "weekly",
  name: "Weekly digest",
  schedule: "0 2 * * 0",
  concurrencyPolicy: "Forbid",
});

const jobRun = (
  id: string,
  status: AstroliftScheduledJobRun["status"],
  startedAt: string,
  durationSeconds: number | null,
  exitCode: number | null
): AstroliftScheduledJobRun => ({
  id,
  registeredAppSlug: "billing",
  environmentName: "production",
  workloadSlug: "weekly",
  k8sJobName: `weekly-${id}`,
  status,
  triggerKind: "scheduled",
  triggeredByMe: false,
  startedAt,
  endedAt: startedAt,
  durationSeconds,
  exitCode,
  logExcerpt: "",
  output: "",
  createdAt: startedAt,
});

export const JOB_RUNS: AstroliftScheduledJobRun[] = [
  jobRun("004", "running", "2026-09-28T02:00:00Z", null, null),
  jobRun("003", "succeeded", "2026-09-21T02:00:00Z", 12, 0),
  jobRun("002", "failed", "2026-09-14T02:00:00Z", 305, 1),
  jobRun("001", "superseded", "2026-09-07T02:00:00Z", 3725, null),
];

export const CRONJOB: CronjobHomeScreenProps = {
  name: "Weekly digest",
  workload: CRON_WORKLOAD,
  runs: JOB_RUNS,
  totalCount: JOB_RUNS.length,
  loading: false,
  error: null,
  onRetry: () => {},
  hasMore: false,
  loadingMore: false,
  onLoadMore: () => {},
};

export const CRONJOB_LOADING: CronjobHomeScreenProps = {
  ...CRONJOB,
  runs: [],
  totalCount: null,
  loading: true,
};

export const CRONJOB_EMPTY: CronjobHomeScreenProps = {
  ...CRONJOB,
  runs: [],
  totalCount: 0,
};

export const CRONJOB_ERROR: CronjobHomeScreenProps = {
  ...CRONJOB,
  runs: [],
  totalCount: null,
  error: "Network error: failed to fetch",
};

/** A long history: weekly runs for most of a year, older pages still on the cursor. */
export const CRONJOB_LONG_HISTORY: CronjobHomeScreenProps = {
  ...CRONJOB,
  runs: Array.from({ length: 40 }, (_, i) =>
    jobRun(
      String(100 - i).padStart(3, "0"),
      i % 9 === 4 ? "failed" : "succeeded",
      new Date(Date.UTC(2026, 8, 28, 2) - i * 7 * 86_400_000).toISOString(),
      12 + i,
      i % 9 === 4 ? 1 : 0
    )
  ),
  totalCount: 120,
  hasMore: true,
};

// ── Task ───────────────────────────────────────────────────────────────────

const taskRun = (
  id: string,
  status: AstroliftTaskRun["status"],
  startedAt: string | null,
  durationSeconds: number | null
): AstroliftTaskRun => ({
  id,
  registeredAppSlug: "billing",
  workloadSlug: "migrate",
  triggerKind: "manual",
  triggeredByUsername: "leo",
  command: ["./manage.py", "migrate"],
  status,
  exitCode: status === "failed" ? 1 : status === "succeeded" ? 0 : null,
  startedAt,
  endedAt: null,
  durationSeconds,
  k8sJobName: `migrate-${id}`,
  createdAt: startedAt ?? "2026-09-28T10:00:00Z",
});

export const TASK_RUNS: AstroliftTaskRun[] = [
  taskRun("r3", "running", "2026-09-28T10:00:00Z", null),
  taskRun("r2", "succeeded", "2026-09-27T10:00:00Z", 48),
  taskRun("r1", "failed", "2026-09-26T10:00:00Z", 7),
];

export const TASK: TaskHomeScreenProps = {
  error: null,
  onRetry: () => {},
  name: "Migrate",
  runs: TASK_RUNS,
  loading: false,
};

// ── Function ───────────────────────────────────────────────────────────────

const START = Date.parse("2026-09-28T12:00:00Z");
const samples = (base: number, swing: number): AstroliftTimeSeriesPoint[] =>
  Array.from({ length: 24 }, (_, i) => ({
    ts: new Date(START + i * 150_000).toISOString(),
    value: Math.max(0, base + Math.sin(i / 3) * swing),
  }));
const signal = (
  name: GoldenSignalKind,
  unit: string,
  points: AstroliftTimeSeriesPoint[]
): AstroliftAppGoldenSignal => ({
  name,
  unit,
  promql: `sum(rate(http_requests_total{app="billing",workload="webhook"}[5m])) /* ${name} */`,
  rangeSeconds: 3600,
  reason: points.length ? "OK" : "NO_DATA_YET",
  samples: points,
});

export const GOLDEN_SIGNALS: GoldenSignalsPanelProps = {
  range: "1h",
  onRangeChange: noop,
  signals: [
    signal("TRAFFIC", "rps", samples(4, 2)),
    signal("ERRORS", "ratio", samples(0.01, 0.005)),
    signal("LATENCY_P50", "seconds", samples(0.04, 0.01)),
    signal("LATENCY_P99", "seconds", samples(0.4, 0.1)),
  ],
  reason: "OK",
  loading: false,
  onRetry: () => undefined,
  statusBreakdown: null,
  statusLoading: false,
  onStatusRetry: () => undefined,
};

export const FUNCTION: FunctionHomeScreenProps = {
  name: "Webhook",
  workload: { isPublic: true },
  host: "webhook.billing.apps.example.com",
  goldenSignals: GOLDEN_SIGNALS,
};

// ── App doctor ─────────────────────────────────────────────────────────────

export const DOCTOR_IDLE: AppDoctorPanelViewProps = {
  report: null,
  loading: false,
  errorMessage: null,
  called: false,
  runChecks: noop,
  repair: async () => {},
  running: null,
};

export const DOCTOR_REPORT: AppDoctorPanelViewProps = {
  ...DOCTOR_IDLE,
  called: true,
  report: {
    healthy: false,
    checks: [
      { key: "manifest", status: "pass", detail: "", fix: "" },
      {
        key: "autowire",
        status: "fail",
        detail: "The deploy workflow file is missing from the default branch.",
        fix: "retry_autowire",
      },
      { key: "registry_repo", status: "pass", detail: "", fix: "" },
      {
        key: "image",
        status: "warn",
        detail: "Running a tag (latest), not a digest.",
        fix: "redeploy",
      },
      {
        key: "dns",
        status: "unknown",
        detail: "Could not resolve billing.apps.example.com: timeout.",
        fix: "",
      },
      { key: "managed_services", status: "skip", detail: "No managed services declared.", fix: "" },
    ],
  },
};
