/**
 * Hand-typed fixtures for the administration insights screens (audit,
 * platform metrics, features), typed against each screen's props.
 */
import { LONG_ARN, LONG_SHA, LONG_URL } from "@/components/list/fixtures";
import type { DeploymentStatus, ScheduledJobRunStatus } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftAuditEvent } from "@/graphql/operations/operations.types";

import type { AuditEventsFeed, AuditLogScreenProps } from "./AuditLogScreen";
import {
  type CombinedRun,
  fromAgentTask,
  fromDeployment,
  fromJobRun,
  fromWorkflowRun,
  mergeRuns,
} from "./combined-runs";
import type { CombinedRunsScreenProps } from "./CombinedRunsScreen";
import type { AdminMetricsScreenProps } from "./AdminMetricsScreen";
import type { PrometheusPanelProps, SystemMetricsPanelProps } from "./ClusterMetricsPanels";
import type { FeaturesScreenProps } from "./FeaturesScreen";
import { WINDOWS, type MetricsCluster, type RangeSeries } from "./metrics-format";

const noop = () => {};

// ─── Audit ────────────────────────────────────────────────────────────

const event = (id: string, patch: Partial<AstroliftAuditEvent> = {}): AstroliftAuditEvent => ({
  id,
  occurredAt: "2026-09-27T14:03:12Z",
  action: "team.create",
  actorDisplay: "Leo Mata",
  actorId: "usr-1a2b3c",
  actorKind: "user",
  targetKind: "team",
  targetSlug: "platform",
  targetId: "tm-91f0",
  decision: "ALLOW",
  data: { name: "Platform", members: 4 },
  before: null,
  after: null,
  requestId: "req-7d3e0c55",
  organizationId: "org-7f3c2a10",
  ...patch,
});

export const AUDIT_EVENTS: AstroliftAuditEvent[] = [
  event("ev-1"),
  event("ev-2", {
    occurredAt: "2026-09-27T13:58:40Z",
    action: "app.env.update",
    targetKind: "app",
    targetSlug: "sales-agents",
    targetId: "app-4410",
    before: { LOG_LEVEL: "info" },
    after: { LOG_LEVEL: "debug" },
  }),
  event("ev-3", {
    occurredAt: "2026-09-27T13:41:05Z",
    action: "cluster.delete",
    actorDisplay: "",
    actorKind: "token",
    actorId: "tok-ci-deploy",
    targetKind: "cluster",
    targetSlug: "conflict-astro",
    targetId: "cl-0081",
    decision: "DENY",
  }),
  event("ev-4", {
    occurredAt: "2026-09-27T12:10:00Z",
    action: "org.login",
    actorKind: "system",
    actorId: "",
    actorDisplay: "",
    targetKind: "",
    targetSlug: "",
    targetId: "",
    decision: "UNKNOWN",
    requestId: "",
  }),
];

const LONG = "a-deliberately-long-identifier-that-keeps-going-well-past-any-column-width";

export const AUDIT_EVENTS_LONG: AstroliftAuditEvent[] = [
  event("ev-long-1", {
    action: `workflow.stage.update.${LONG}`,
    actorDisplay: "Someone With An Exceptionally Long Display Name From A Federated Directory",
    actorId: `usr-${LONG}`,
    targetKind: "workflow",
    targetSlug: LONG,
    targetId: `wf-${LONG}`,
    requestId: `req-${LONG}-${LONG}`,
    before: { prompt: LONG.repeat(4) },
    after: { prompt: LONG.repeat(6) },
  }),
  // The three the list archetype must hold: a 64-char SHA, a 200-char ARN
  // and an unbroken URL.
  event("ev-long-2", {
    action: "deployment.approve",
    actorKind: "token",
    actorDisplay: "",
    actorId: LONG_URL,
    targetKind: "iam_role",
    targetSlug: LONG_ARN,
    targetId: LONG_SHA,
    requestId: LONG_SHA,
    decision: "DENY",
  }),
];

/** Everything but the list state, which each story makes with useLocalListState. */
/** The audit feed: the newest page, more behind the cursor. */
export const AUDIT_FEED: AuditEventsFeed = {
  items: AUDIT_EVENTS,
  loading: false,
  error: null,
  onRetry: noop,
  hasMore: true,
  loadingMore: false,
  onLoadMore: noop,
  newCount: 0,
  onShowNew: noop,
};

export const AUDIT: Omit<AuditLogScreenProps, "list"> = {
  events: AUDIT_FEED,
  totalCount: 1284,
  targetFilteredLocally: false,
  retentionDays: 365,
  exporting: false,
  onExport: async () => {},
  canEditRetention: true,
  savingRetention: false,
  saveRetention: async () => true,
};

// ─── Run audit ────────────────────────────────────────────────────────

// Built through the real mappers, so the fixtures carry what each source
// actually says (and does not say) about who started a run.
const at = (minutesAgo: number) =>
  new Date(Date.UTC(2026, 8, 27, 14, 0, 0) - minutesAgo * 60_000).toISOString();

const agentRun = (i: number, status: string) =>
  fromAgentTask({
    id: `task-${(0x7f3c2a91 - i * 0x1b3d).toString(16)}`,
    agentSlug: ["support-bot", "triage-agent", "billing-reconciler"][i % 3],
    agentName: "Support bot",
    projectSlug: i % 2 ? "storefront" : "internal-tools",
    status,
    createdAt: at(i * 11 + 1),
    startedAt: at(i * 11),
    finishedAt: status === "running" ? null : at(i * 11 - 2),
  });

const workflowRun = (i: number, status: string, child = false) =>
  fromWorkflowRun({
    guid: `wfr-${(0x51e0a7 + i * 0x2f1).toString(16)}`,
    definitionGuid: "wfd-1",
    definitionSlug: "nightly-sync",
    definitionName: "Nightly sync",
    projectGuid: "prj-1",
    projectSlug: "internal-tools",
    status,
    temporalWorkflowId: "tw-1",
    temporalRunId: null,
    currentStageOrder: null,
    currentStageRole: "",
    parentRunGuid: child ? "wfr-parent" : null,
    parentStageExecutionGuid: null,
    nestingDepth: child ? 1 : 0,
    childRunCount: 0,
    startedAt: at(i * 13 + 4),
    endedAt: status === "running" ? null : at(i * 13 + 1),
  });

const deploymentRun = (i: number, status: string, mine = false) =>
  fromDeployment({
    id: `dep-${(0x1112015d + i * 0x3b).toString(16)}`,
    registeredAppSlug: ["checkout", "storefront-web"][i % 2],
    environmentName: i % 3 ? "production" : "staging",
    workloadSlug: "web",
    triggerKind: (["push", "manual", "ci"] as const)[i % 3],
    status: status as DeploymentStatus,
    startedAt: at(i * 17 + 6),
    createdAt: at(i * 17 + 7),
    durationSeconds: 84 + i * 9,
    commitAuthor: mine ? "" : "grace@example.com",
    ciProvider: i % 3 === 2 ? "github" : "",
    triggeredByUserId: mine ? "ops@example.com" : null,
    triggeredByMe: mine,
  });

const jobRun = (i: number, status: ScheduledJobRunStatus) =>
  fromJobRun({
    id: `job-${(0x9a01 + i * 0x77).toString(16)}`,
    registeredAppSlug: "checkout",
    environmentName: "production",
    workloadSlug: "nightly-invoices",
    status,
    startedAt: at(i * 29 + 9),
    createdAt: at(i * 29 + 9),
    durationSeconds: status === "running" ? null : 312 + i,
  });

export const COMBINED_RUNS: CombinedRun[] = mergeRuns(
  [
    agentRun(0, "running"),
    agentRun(1, "completed"),
    agentRun(2, "failed"),
    agentRun(3, "timed_out"),
  ],
  [workflowRun(0, "running"), workflowRun(1, "completed", true), workflowRun(2, "failed")],
  [
    deploymentRun(0, "deploying", true),
    deploymentRun(1, "running"),
    deploymentRun(2, "pending_approval"),
    deploymentRun(3, "rolled_back"),
    deploymentRun(4, "superseded", true),
  ],
  [jobRun(0, "succeeded"), jobRun(1, "failed"), jobRun(2, "running")]
);

/** A 64-char SHA, a 200-char ARN and an unbroken URL in the cells that hold them. */
export const COMBINED_RUNS_LONG: CombinedRun[] = [
  { ...COMBINED_RUNS[0], key: "long-1", id: LONG_SHA, subject: LONG_ARN, scope: LONG_URL },
  {
    ...COMBINED_RUNS[1],
    key: "long-2",
    startedBy: `${LONG}@example.com`,
    trigger: LONG_URL,
    status: `ImagePullBackOff:${LONG}`,
  },
  ...COMBINED_RUNS.slice(2, 5),
];

/** Everything but the list state, which each story makes with useLocalListState. */
export const RUN_AUDIT: Omit<CombinedRunsScreenProps, "list"> = {
  rows: COMBINED_RUNS,
  loading: false,
  stale: false,
  error: null,
  onRetry: noop,
  nextCursor: "o:25",
  totalCount: 318,
  approximateCount: true,
  unavailable: [],
  coverage: "Merged from the newest 100 of each kind; older runs are on each kind's own list.",
  onExportCsv: noop,
};

// ─── Platform metrics ────────────────────────────────────────────────

const cluster = (id: string, patch: Partial<MetricsCluster> = {}): MetricsCluster => ({
  id,
  slug: "conflict-astro",
  name: "CONFLICT Astro",
  providerPluginSlug: "aws-eks",
  region: "us-east-2",
  isActive: true,
  heartbeatStatus: "connected",
  heartbeatAgeSeconds: 12,
  ...patch,
});

export const CLUSTERS: MetricsCluster[] = [
  cluster("cl-1"),
  cluster("cl-2", {
    slug: "edge-gke",
    name: "Edge GKE",
    providerPluginSlug: "gcp-gke",
    region: "us-central1",
    heartbeatStatus: "degraded",
    heartbeatAgeSeconds: 240,
  }),
  cluster("cl-3", {
    slug: "onprem-lab",
    name: "On-prem lab",
    providerPluginSlug: "onprem",
    region: "",
    heartbeatStatus: "offline",
    heartbeatAgeSeconds: 7_200,
  }),
  cluster("cl-4", {
    slug: "new-aks",
    name: "New AKS",
    providerPluginSlug: "azure-aks",
    region: "eastus",
    heartbeatStatus: "never_seen",
    heartbeatAgeSeconds: null,
  }),
];

export const CLUSTERS_LONG: MetricsCluster[] = [
  cluster("cl-long", {
    slug: `cluster-${LONG}`,
    name: "A cluster name long enough to wrap the header row several times over",
    providerPluginSlug: `provider-${LONG}`,
    region: `region-${LONG}`,
  }),
  cluster("cl-long-off", {
    slug: `offline-${LONG}`,
    name: "Offline cluster with a long name",
    heartbeatStatus: "offline",
    heartbeatAgeSeconds: 900_000,
  }),
];

const points = (base: number, spread: number, n = 24) =>
  Array.from({ length: n }, (_, i) => ({
    ts: 1_790_000_000 + i * 60,
    value: base + spread * Math.sin(i / 3),
  }));

const series = (
  metric: string,
  label: string,
  unit: string,
  current: number | null,
  pts = points(current ?? 0, (current ?? 0) * 0.2)
): RangeSeries => ({ metric, label, unit, current, points: pts });

export const PROM_SERIES: RangeSeries[] = [
  series("cpu_utilization", "CPU", "ratio", 0.42),
  series("memory_utilization", "Memory", "ratio", 0.78),
  series("pod_running_ratio", "Pods running", "ratio", 0.96),
  series("restart_rate", "Restarts", "count", 0),
  series("deployment_ready_ratio", "Deployments ready", "ratio", 0.62),
  series("network_rx", "Network in", "bytes_per_sec", 3_400_000),
];

export const SYSTEM_SERIES: RangeSeries[] = [
  series("request_rate", "Requests", "rps", 142),
  series("error_rate", "Error rate", "ratio", 0.003),
  series("latency_p99", "Latency p99", "seconds", 0.34),
];

export const PROMETHEUS: PrometheusPanelProps = {
  slug: "conflict-astro",
  instant: {
    available: true,
    reason: null,
    nodeCount: 6,
    podRunningRatio: 0.96,
    cpuUtilization: 0.42,
    memoryUtilization: 0.93,
    deploymentReadyRatio: 0.62,
  },
  instantLoading: false,
  range: {
    available: true,
    reason: null,
    rangeSeconds: 3_600,
    stepSeconds: 60,
    series: PROM_SERIES,
  },
  rangeLoading: false,
};

export const PROMETHEUS_LOADING: PrometheusPanelProps = {
  slug: "conflict-astro",
  instant: null,
  instantLoading: true,
  range: null,
  rangeLoading: true,
};

export const PROMETHEUS_NO_ENDPOINT: PrometheusPanelProps = {
  ...PROMETHEUS,
  instant: { ...PROMETHEUS.instant!, available: false, reason: "no_endpoint" },
  range: { ...PROMETHEUS.range!, available: false, reason: "no_endpoint", series: [] },
};

export const PROMETHEUS_UNREACHABLE: PrometheusPanelProps = {
  ...PROMETHEUS,
  instant: { ...PROMETHEUS.instant!, available: false, reason: "unreachable" },
  range: { ...PROMETHEUS.range!, available: false, reason: "unreachable", series: [] },
};

/** Instant is fine, the range query is not: the one case with a second callout. */
export const PROMETHEUS_RANGE_DOWN: PrometheusPanelProps = {
  ...PROMETHEUS,
  range: { ...PROMETHEUS.range!, available: false, reason: "unreachable", series: [] },
};

export const PROMETHEUS_SPARSE: PrometheusPanelProps = {
  ...PROMETHEUS,
  range: {
    ...PROMETHEUS.range!,
    series: [
      series("cpu_utilization", "CPU", "ratio", null, []),
      series("memory_utilization", "Memory", "ratio", 0.5, points(0.5, 0, 1)),
    ],
  },
};

export const PROMETHEUS_LONG: PrometheusPanelProps = {
  ...PROMETHEUS,
  slug: `cluster-${LONG}`,
  range: {
    ...PROMETHEUS.range!,
    series: [
      series(
        "latency_p95",
        `A metric label that goes on and on ${LONG}`,
        "seconds",
        12.5,
        points(12.5, 3)
      ),
      series("network_rx", "Network in", "bytes_per_sec", 9_800_000_000),
    ],
  },
};

export const SYSTEM: SystemMetricsPanelProps = {
  providerSlug: "aws-eks",
  metrics: {
    available: true,
    reason: null,
    source: "cloudwatch",
    appNamespace: "sales-agents",
    rangeSeconds: 3_600,
    stepSeconds: 60,
    series: SYSTEM_SERIES,
  },
  loading: false,
};

export const SYSTEM_LOADING: SystemMetricsPanelProps = {
  providerSlug: "aws-eks",
  metrics: null,
  loading: true,
};

export const SYSTEM_NOT_SUPPORTED: SystemMetricsPanelProps = {
  providerSlug: "onprem",
  metrics: { ...SYSTEM.metrics!, available: false, reason: "not_supported", series: [] },
  loading: false,
};

export const SYSTEM_UNREACHABLE: SystemMetricsPanelProps = {
  ...SYSTEM,
  metrics: { ...SYSTEM.metrics!, available: false, reason: "unreachable", series: [] },
};

export const METRICS: Omit<AdminMetricsScreenProps, "renderLivePanels"> = {
  windowLabel: "1h",
  onWindowChange: noop,
  win: WINDOWS[0],
  clusters: CLUSTERS,
  loading: false,
  error: null,
  onRetry: noop,
};

// ─── Features ────────────────────────────────────────────────────────

export const FEATURES: FeaturesScreenProps = {
  runtimeFlags: [
    {
      key: "zentinelle.enabled",
      enabled: true,
      description: "Show Zentinelle governance surfaces and route agents through its gateway.",
    },
    {
      key: "admin.cost_enabled",
      enabled: false,
      description: "Expose the cost explorer to organization admins.",
    },
    { key: "workflows.graph_view", enabled: true, description: null },
  ],
  buildTimeFeatures: [
    {
      key: "gpu.model_hosting",
      enabled: false,
      envVar: "ASTROLIFT_GPU_MODEL_HOSTING",
      description: "Load the vLLM model-hosting app at boot.",
    },
    {
      key: "billing.enabled",
      enabled: true,
      envVar: "ASTROLIFT_BILLING_ENABLED",
      description: null,
    },
  ],
  loading: false,
  error: null,
  onRetry: noop,
  pendingKey: null,
  toggleFlag: async () => {},
};

export const FEATURES_LONG: FeaturesScreenProps = {
  ...FEATURES,
  runtimeFlags: [
    {
      key: `experimental.${LONG.replace(/-/g, "_")}`,
      enabled: true,
      description: `A description that runs long enough to wrap several times. ${LONG} ${LONG}`,
    },
  ],
  buildTimeFeatures: [
    {
      key: `install.${LONG.replace(/-/g, "_")}`,
      enabled: false,
      envVar: `ASTROLIFT_${LONG.replace(/-/g, "_").toUpperCase()}`,
      description: LONG,
    },
  ],
};
