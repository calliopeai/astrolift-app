import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import type {
  AstroliftAppHealthSummary,
  AstroliftDeploymentMetrics,
} from "@/graphql/lifecycle/lifecycle.types";
import type {
  AstroliftAuditEvent,
  AstroliftWorkflowRun,
} from "@/graphql/operations/operations.types";

import type { MetricsScreenProps } from "../metrics/MetricsScreen";
import type { AppErrorScreenProps } from "../shell/AppErrorScreen";

import type { OpsScreenProps } from "./OpsScreen";
import type { OpsAlertEvent } from "./use-ops";

/** The JSON scalar is typed as an object; real values can be any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

const LONG_NAME =
  "payments-reconciliation-and-settlement-batch-processor-with-an-unreasonably-long-name";

// ─── Metrics ──────────────────────────────────────────────────────────

export const DEPLOYMENT_METRICS: AstroliftDeploymentMetrics = {
  windowDays: 30,
  total: 42,
  succeeded: 37,
  failed: 3,
  rolledBack: 2,
  inFlight: 2,
  successRate: 37 / 40,
  meanDurationSeconds: 94,
  p95DurationSeconds: 211,
  dailySucceeded: [
    1, 2, 0, 3, 1, 2, 2, 0, 1, 4, 2, 1, 0, 3, 2, 1, 1, 2, 0, 1, 2, 1, 0, 1, 2, 1, 0, 1, 0, 0,
  ],
  dailyFailed: [
    0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
  ],
  dailyMeanDurationSeconds: [
    80,
    95,
    null,
    120,
    70,
    88,
    90,
    null,
    101,
    140,
    85,
    92,
    null,
    99,
    87,
    76,
    81,
    130,
    null,
    70,
    90,
    84,
    null,
    79,
    93,
    88,
    null,
    91,
    null,
    null,
  ],
};

export const NO_ROLLOUTS_METRICS: AstroliftDeploymentMetrics = {
  windowDays: 30,
  total: 0,
  succeeded: 0,
  failed: 0,
  rolledBack: 0,
  inFlight: 0,
  successRate: -1,
  meanDurationSeconds: null,
  p95DurationSeconds: null,
  dailySucceeded: Array(30).fill(0),
  dailyFailed: Array(30).fill(0),
  dailyMeanDurationSeconds: Array(30).fill(null),
};

export const APP_HEALTH: AstroliftAppHealthSummary[] = [
  {
    appSlug: "storefront",
    appName: "Storefront",
    environmentCount: 3,
    latestDeploymentStatus: "running",
    hasRecentFailure: false,
    latestImageTag: "v2.14.0",
    lastDeployedAt: "2026-09-27T18:04:00Z",
    primitiveKind: "app",
  },
  {
    appSlug: "billing-api",
    appName: "Billing API",
    environmentCount: 2,
    latestDeploymentStatus: "failed",
    hasRecentFailure: true,
    latestImageTag: "sha-4f1c9e2",
    lastDeployedAt: "2026-09-28T09:12:00Z",
    primitiveKind: "app",
  },
  {
    appSlug: "support-agent",
    appName: "Support agent",
    environmentCount: 1,
    latestDeploymentStatus: "pending_approval",
    hasRecentFailure: false,
    latestImageTag: "v0.3.1",
    lastDeployedAt: "2026-09-26T14:30:00Z",
    primitiveKind: "agent",
  },
  {
    appSlug: "docs",
    appName: "Docs",
    environmentCount: 0,
    latestDeploymentStatus: null,
    hasRecentFailure: false,
    latestImageTag: "",
    lastDeployedAt: null,
    primitiveKind: "app",
  },
];

export const METRICS: MetricsScreenProps = {
  windowDays: 30,
  metrics: DEPLOYMENT_METRICS,
  metricsLoading: false,
  apps: APP_HEALTH,
  healthLoading: false,
};

export const METRICS_LONG_APPS: AstroliftAppHealthSummary[] = [
  {
    appSlug: LONG_NAME,
    appName: `${LONG_NAME} (production mirror)`,
    environmentCount: 12,
    latestDeploymentStatus: "rolled_back",
    hasRecentFailure: true,
    latestImageTag: `registry.example.com/platform/${LONG_NAME}:sha-4f1c9e2b7a8d6e5f`,
    lastDeployedAt: "2026-09-28T09:12:00Z",
    primitiveKind: "app",
  },
];

// ─── Ops ──────────────────────────────────────────────────────────────

function cluster(overrides: Partial<AstroliftTenantCluster>): AstroliftTenantCluster {
  return {
    id: "c-1",
    slug: "prod-us-east",
    name: "Production US East",
    providerPluginSlug: "aws-eks",
    region: "us-east-1",
    authMethod: "irsa",
    isActive: true,
    capabilitiesProbedAt: "2026-09-28T08:00:00Z",
    agentProvisioned: true,
    albAuthConfig: null,
    bootstrapRuns: [],
    capabilities: json({}),
    createdAt: "2026-06-01T12:00:00Z",
    endpoint: "https://k8s.example.com",
    heartbeatAgeSeconds: 12,
    heartbeatIntervalSeconds: 30,
    heartbeatStatus: "connected",
    ingressClass: "alb",
    ingressMode: "shared",
    lastBootstrapRun: null,
    lastHeartbeatAt: "2026-09-28T09:59:48Z",
    lastManagementError: "",
    lifecycle: "managed",
    managedAt: "2026-06-01T12:30:00Z",
    oidcAuthConfig: null,
    organizationSlug: "acme",
    secretsBackendProvisionedAt: null,
    ...overrides,
  };
}

export const CLUSTERS: AstroliftTenantCluster[] = [
  cluster({}),
  cluster({
    id: "c-2",
    slug: "staging-eu",
    name: "Staging EU",
    providerPluginSlug: "gcp-gke",
    region: "europe-west1",
    authMethod: "workload-identity",
    capabilitiesProbedAt: null,
  }),
  cluster({
    id: "c-3",
    slug: "onprem-lab",
    name: "On-prem lab",
    providerPluginSlug: "kubeconfig",
    region: "dc-1",
    authMethod: "kubeconfig",
    isActive: false,
  }),
];

export const OPS_METRICS: AstroliftDeploymentMetrics = {
  ...DEPLOYMENT_METRICS,
  windowDays: 1,
  total: 6,
  succeeded: 4,
  failed: 1,
  rolledBack: 0,
  inFlight: 1,
  successRate: 0.8,
  meanDurationSeconds: 88,
  p95DurationSeconds: 164,
  dailySucceeded: [4],
  dailyFailed: [1],
  dailyMeanDurationSeconds: [88],
};

export const ALERTS: OpsAlertEvent[] = [
  {
    id: "a-1",
    ruleId: "r-1",
    severity: "critical",
    firedAt: "2026-09-28T09:40:00Z",
    resolvedAt: null,
    acknowledgedAt: null,
    summary: "billing-api error rate above 5% for 10 minutes",
    detail: {},
  },
  {
    id: "a-2",
    ruleId: "r-2",
    severity: "warning",
    firedAt: "2026-09-28T08:15:00Z",
    resolvedAt: null,
    acknowledgedAt: "2026-09-28T08:20:00Z",
    summary: "staging-eu has not been probed",
    detail: {},
  },
];

export const RUNS: AstroliftWorkflowRun[] = [
  {
    id: "w-1",
    workflowId: "deploy-storefront-1",
    runId: "run-1",
    workflowKind: "DeployWorkflow",
    status: "completed",
    startedAt: "2026-09-28T09:00:00Z",
    endedAt: "2026-09-28T09:01:34Z",
    failure: json({}),
    organizationId: "org-1",
    registeredAppId: "app-1",
  },
  {
    id: "w-2",
    workflowId: "deploy-billing-api-7",
    runId: "run-2",
    workflowKind: "DeployWorkflow",
    status: "failed",
    startedAt: "2026-09-28T09:10:00Z",
    endedAt: "2026-09-28T09:12:00Z",
    failure: json({ message: "ImagePullBackOff: sha-4f1c9e2 not found" }),
    organizationId: "org-1",
    registeredAppId: "app-2",
  },
  {
    id: "w-3",
    workflowId: "preview-docs-42",
    runId: "run-3",
    workflowKind: "PreviewWorkflow",
    status: "running",
    startedAt: "2026-09-28T09:55:00Z",
    endedAt: null,
    failure: json({}),
    organizationId: "org-1",
    registeredAppId: null,
  },
];

function auditEvent(overrides: Partial<AstroliftAuditEvent>): AstroliftAuditEvent {
  return {
    id: "e-1",
    occurredAt: "2026-09-28T09:58:00Z",
    action: "deployment.create",
    actorDisplay: "leo@example.com",
    actorId: "u-1",
    actorKind: "user",
    targetKind: "app",
    targetSlug: "storefront",
    targetId: "app-1",
    decision: "ALLOW",
    data: json({}),
    before: null,
    after: null,
    organizationId: "org-1",
    requestId: "req-1",
    ...overrides,
  };
}

export const AUDIT: AstroliftAuditEvent[] = [
  auditEvent({}),
  auditEvent({
    id: "e-2",
    action: "cluster.delete",
    actorDisplay: "",
    actorKind: "service_token",
    targetKind: "cluster",
    targetSlug: "prod-us-east",
    decision: "DENY",
  }),
  auditEvent({
    id: "e-3",
    action: "session.refresh",
    targetKind: "",
    targetSlug: "",
    decision: "UNKNOWN",
  }),
];

export const OPS: OpsScreenProps = {
  clusters: CLUSTERS,
  clustersLoading: false,
  metrics: OPS_METRICS,
  metricsLoading: false,
  alerts: ALERTS,
  alertsLoading: false,
  audit: AUDIT,
  auditLoading: false,
  runs: RUNS,
  runsLoading: false,
};

export const OPS_LOADING: OpsScreenProps = {
  clusters: [],
  clustersLoading: true,
  metrics: undefined,
  metricsLoading: true,
  alerts: [],
  alertsLoading: true,
  audit: [],
  auditLoading: true,
  runs: [],
  runsLoading: true,
};

export const OPS_EMPTY: OpsScreenProps = {
  clusters: [],
  clustersLoading: false,
  metrics: { ...NO_ROLLOUTS_METRICS, windowDays: 1 },
  metricsLoading: false,
  alerts: [],
  alertsLoading: false,
  audit: [],
  auditLoading: false,
  runs: [],
  runsLoading: false,
};

export const OPS_LONG: OpsScreenProps = {
  ...OPS,
  clusters: [
    cluster({
      slug: LONG_NAME,
      name: `${LONG_NAME} cluster`,
      providerPluginSlug: "aws-eks-with-a-custom-provider-plugin",
      region: "ap-southeast-4-melbourne-local-zone",
      authMethod: "assume-role-with-web-identity-and-external-id",
    }),
  ],
  alerts: [
    {
      ...ALERTS[0],
      summary: `${LONG_NAME} has exceeded its error budget burn rate across every environment it runs in`,
      severity: "critical-page-the-on-call",
    },
  ],
  runs: [
    {
      ...RUNS[1],
      workflowKind: "ReconcileAndPromoteAcrossEnvironmentsWorkflow",
      failure: json({
        message: `${LONG_NAME}: ImagePullBackOff after 12 retries; registry returned 401 Unauthorized`,
        stack: ["activity.pull_image", "activity.apply_manifest"],
      }),
    },
  ],
  audit: [
    auditEvent({
      action: "deployment.promote.across.environments.with.approval",
      actorDisplay: "a-very-long-service-account-name@automation.example.com",
      targetKind: "app",
      targetSlug: LONG_NAME,
    }),
  ],
};

// ─── Shell ────────────────────────────────────────────────────────────

export const APP_ERROR: Omit<AppErrorScreenProps, "onReset"> = {
  message: "Failed to fetch: GraphQL request to /graphql returned 502 Bad Gateway",
  digest: "3141592653",
};

export const APP_ERROR_LONG: Omit<AppErrorScreenProps, "onReset"> = {
  message: `Cannot read properties of undefined (reading 'environments') while rendering ${LONG_NAME} at DeploymentTimeline > EnvironmentRow > StatusCell`,
  digest: "a8f2c1d9e0b7465f93c2e1d0a9b8c7d6e5f4a3b2c1d0e9f8",
};
