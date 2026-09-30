import {
  DEPLOY_DEPLOYING,
  DEPLOY_FAILED,
  DEPLOY_LONG,
  DEPLOY_PENDING_APPROVAL,
  DEPLOY_RUNNING,
  DEPLOYMENTS,
  LONG,
} from "@/components/screens/apps/deployments/app-deployments-logs.fixtures";
import type {
  AstroliftAppEnvironment,
  AstroliftDeployment,
  AstroliftDeploymentLogEntry,
} from "@/graphql/lifecycle/lifecycle.types";

import type { DeploymentDetailScreenProps } from "./DeploymentDetailScreen";
import type { DeploymentsScreenProps } from "./DeploymentsScreen";
import type { StartDeploymentPageProps } from "./StartDeploymentPage";

/** Hand-typed fixtures for /deployments, /deployments/[id] and /deployments/new. */

export { LONG };

/** A 64-character SHA and a 200-character ARN, for the long-string stories. */
export const SHA_64 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";
export const ARN_200 =
  `arn:aws:iam::123456789012:role/${"astrolift-tenant-deployer-".repeat(7)}end`.slice(0, 200);

const noop = () => {};
const asyncNoop = async () => {};
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

/** The generated JSON scalar is typed Record<string, unknown>; real values are any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

// ---------------------------------------------------------------- list

export const TRIGGERED_BY_OTHER: AstroliftDeployment = {
  ...DEPLOY_PENDING_APPROVAL,
  triggerKind: "manual",
  triggeredByMe: false,
};

/** Triggered by the viewer: what Mine keeps. */
export const TRIGGERED_BY_ME: AstroliftDeployment = {
  ...DEPLOY_RUNNING,
  id: "d3b07384-d9a0-4c9b-8f1e-000000000021",
  registeredAppSlug: "checkout",
  triggerKind: "manual",
  triggeredByMe: true,
};

export const ROWS: AstroliftDeployment[] = [
  DEPLOY_DEPLOYING,
  TRIGGERED_BY_OTHER,
  TRIGGERED_BY_ME,
  DEPLOY_RUNNING,
  {
    ...DEPLOY_RUNNING,
    id: "d3b07384-d9a0-4c9b-8f1e-000000000020",
    registeredAppSlug: "billing",
    environmentName: "prod",
    workloadSlug: "worker",
  },
  DEPLOY_FAILED,
];

/** Everything DeploymentsScreen takes except the list state, which a story makes. */
export function listProps(
  overrides: Partial<Omit<DeploymentsScreenProps, "list">> = {}
): Omit<DeploymentsScreenProps, "list"> {
  const rows = overrides.rows ?? ROWS;
  return {
    rows,
    newRows: { count: 0, onReveal: noop },
    loading: false,
    stale: false,
    error: null,
    onRetry: noop,
    nextCursor: "c:25",
    totalCount: 140,
    deploymentsById: new Map(rows.map((d) => [d.id, d])),
    canDeploy: true,
    canApprove: true,
    canRollback: true,
    busy: false,
    bulkRunning: false,
    runAction: asyncNoop,
    runBulk: asyncNoop,
    startHref: "/deployments/new",
    ...overrides,
  };
}

export const LIST_LONG_ROWS: AstroliftDeployment[] = [
  { ...DEPLOY_LONG, registeredAppSlug: LONG, commitSha: "a".repeat(64) },
  { ...DEPLOY_RUNNING, registeredAppSlug: LONG, workloadSlug: LONG, imageTag: `sha-${LONG}` },
];

export { DEPLOYMENTS as HISTORY_ROWS };

// ---------------------------------------------------------------- start page

const ENVIRONMENTS: AstroliftAppEnvironment[] = [
  {
    id: "env-prod",
    name: "prod",
    registeredAppSlug: "storefront",
    kind: "production",
    region: "us-west-2",
    ownedByMe: false,
    createdAt: minutesAgo(60 * 24 * 30),
    deploysPaused: false,
    ingressPaused: false,
    requiredApprovals: 2,
    settings: [],
    url: "https://storefront.example.com",
  },
  {
    id: "env-stg",
    name: "stg",
    registeredAppSlug: "storefront",
    kind: "other",
    region: "us-west-2",
    ownedByMe: false,
    createdAt: minutesAgo(60 * 24 * 30),
    deploysPaused: true,
    ingressPaused: false,
    requiredApprovals: 0,
    settings: [],
    url: "https://stg.storefront.example.com",
  },
];

export const START: StartDeploymentPageProps = {
  apps: [
    { id: "app-1", slug: "storefront", name: "Storefront" },
    { id: "app-2", slug: "billing", name: "Billing" },
  ],
  appsLoading: false,
  appsError: null,
  environments: ENVIRONMENTS,
  environmentsLoading: false,
  appSlug: "storefront",
  setAppSlug: noop,
  submitting: false,
  onSubmit: async () => ({ ok: true }),
  cancelHref: "/deployments",
};

export const START_LONG: StartDeploymentPageProps = {
  ...START,
  apps: [{ id: "app-long", slug: LONG, name: `${LONG} ${LONG}` }],
  environments: [{ ...ENVIRONMENTS[0], name: `preview-${LONG}` }],
  appSlug: LONG,
};

// ---------------------------------------------------------------- detail

const LOG: AstroliftDeploymentLogEntry[] = [
  {
    id: "fl1",
    phase: "",
    event: "",
    deploymentId: DEPLOY_RUNNING.id,
    occurredAt: minutesAgo(42),
    status: "pending",
    message: "Deployment queued.",
    detail: {},
  },
  {
    id: "fl2",
    phase: "",
    event: "",
    deploymentId: DEPLOY_RUNNING.id,
    occurredAt: minutesAgo(41),
    status: "deploying",
    message: "Applying manifests to prod-west.",
    detail: json({ replicas: 3, strategy: "rolling" }),
  },
  {
    id: "fl3",
    phase: "",
    event: "",
    deploymentId: DEPLOY_RUNNING.id,
    occurredAt: minutesAgo(39),
    status: "running",
    message: "3 of 3 replicas ready.",
    detail: {},
  },
];

const phase = (name: string, from: number, to: number | null = null, failed = false) => ({
  name,
  startedAt: minutesAgo(from),
  completedAt: to == null || failed ? null : minutesAgo(to),
  failedAt: failed && to != null ? minutesAgo(to) : null,
  healthyAt: name === "health" && to != null && !failed ? minutesAgo(to) : null,
});
const OBSERVED_PHASES = [
  phase("build", 42, 41),
  { ...phase("push", 42, 41), startedAt: null },
  phase("apply", 41, 40),
  phase("rollout", 40, 39),
  phase("health", 39, 39),
];

export const DETAIL: DeploymentDetailScreenProps = {
  deployment: {
    ...DEPLOY_RUNNING,
    phases: OBSERVED_PHASES,
    approvalsRequired: 1,
    approvalsReceived: 1,
  },
  loading: false,
  error: null,
  onRetry: noop,
  now: Date.now(),
  log: LOG,
  logLoading: false,
  logError: null,
  onRetryLog: noop,
  onDownload: asyncNoop,
  hasOlderLog: false,
  onLoadOlderLog: asyncNoop,
  manifest: {
    appSlug: "storefront",
    environmentName: "prod",
    imageTag: DEPLOY_RUNNING.imageTag,
    namespace: "storefront-prod",
    resources: json([
      { apiVersion: "apps/v1", kind: "Deployment", metadata: { name: "web" } },
      { apiVersion: "v1", kind: "Service", metadata: { name: "web" } },
    ]),
  },
  manifestLoading: false,
  events: [
    {
      id: "ev1",
      eventType: "deployment.succeeded",
      payload: {},
      registeredAppId: "app-1",
      occurredAt: minutesAgo(39),
    },
    {
      id: "ev2",
      eventType: "deployment.started",
      payload: {},
      registeredAppId: "app-1",
      occurredAt: minutesAgo(42),
    },
  ],
  eventsLoading: false,
  approvalHistory: [
    {
      id: "ah1",
      action: "proposed",
      decision: "none",
      actorKind: "user",
      actorId: "u1",
      actorDisplay: "leo@example.com",
      occurredAt: minutesAgo(44),
      reason: "",
    },
    {
      id: "ah2",
      action: "approved",
      decision: "approved",
      actorKind: "user",
      actorId: "u2",
      actorDisplay: "ops@example.com",
      occurredAt: minutesAgo(43),
      reason: "Looks good after staging soak.",
    },
  ],
  approvalHistoryLoading: false,
  releaseNotes: {
    baseSha: "1a2b3c4d5e6f",
    headSha: DEPLOY_RUNNING.commitSha,
    compareUrl: "https://github.com/example/storefront/compare/1a2b3c4...4f2a9c1",
    pullRequests: [
      {
        number: 412,
        title: "Tighten checkout retry budget",
        body: "Caps retries at 3 and adds jitter so a slow payment provider cannot pin the pool.",
        author: "leo",
        mergedAt: minutesAgo(50),
        prUrl: "https://github.com/example/storefront/pull/412",
      },
    ],
    commits: [
      {
        sha: "4f2a9c1e7b3d",
        subject: "Tighten checkout retry budget",
        author: "leo",
        isMerge: false,
      },
      { sha: "9e8d7c6b5a4f", subject: "Merge pull request #412", author: "leo", isMerge: true },
    ],
  },
  canApprove: true,
  canDeploy: true,
  canRollback: true,
  busy: false,
  onApprove: asyncNoop,
  onAbort: asyncNoop,
  onRollback: asyncNoop,
  onRedeploy: asyncNoop,
  onDelete: asyncNoop,
};

export const DETAIL_LOADING: DeploymentDetailScreenProps = {
  ...DETAIL,
  deployment: null,
  loading: true,
};

export const DETAIL_NOT_FOUND: DeploymentDetailScreenProps = {
  ...DETAIL,
  deployment: null,
  loading: false,
};

/** Just queued: no log, events, trail, notes or manifest yet. */
export const DETAIL_EMPTY: DeploymentDetailScreenProps = {
  ...DETAIL,
  deployment: { ...DEPLOY_PENDING_APPROVAL, commitMessage: "" },
  log: [],
  manifest: null,
  events: [],
  approvalHistory: [],
  releaseNotes: null,
};

/** A failed rollout whose manifest also failed to render. */
export const DETAIL_FAILED: DeploymentDetailScreenProps = {
  ...DETAIL,
  deployment: {
    ...DEPLOY_FAILED,
    phases: [...OBSERVED_PHASES.slice(0, 4), phase("health", 39, 38, true)],
  },
  log: [
    ...LOG.slice(0, 2),
    {
      id: "fl4",
      phase: "",
      event: "",
      deploymentId: DEPLOY_FAILED.id,
      occurredAt: minutesAgo(60 * 5 - 2),
      status: "failed",
      message: "Readiness probe failed on 2 of 3 replicas.",
      detail: json({ probe: "readiness", failing: ["web-0", "web-2"] }),
    },
  ],
  manifest: {
    appSlug: "storefront",
    environmentName: "prod",
    imageTag: DEPLOY_FAILED.imageTag,
    namespace: "storefront-prod",
    resources: {},
    error:
      'template: deployment.yaml:14:22: executing "deployment.yaml" at <.Values.image>: nil pointer',
    errorPath: "chart/templates/deployment.yaml",
    errorLine: 14,
  },
};

export const DETAIL_LONG: DeploymentDetailScreenProps = {
  ...DETAIL,
  deployment: {
    ...DEPLOY_LONG,
    registeredAppSlug: LONG,
    commitSha: SHA_64,
    buildError: "",
    statusReason: `AccessDenied: not authorized to perform ecr:BatchGetImage on ${ARN_200}`,
    commitMessage: `${LONG} ${LONG}\n\n${LONG}`,
    ciRunUrl: `https://ci.example.com/${LONG}/runs/1`,
  },
  log: [
    ...LOG.slice(0, 2).map((e) => ({ ...e, message: `${e.message} ${LONG}` })),
    {
      id: "fl9",
      phase: "",
      event: "",
      deploymentId: DEPLOY_LONG.id,
      occurredAt: minutesAgo(38),
      status: "failed",
      message: `Image pull denied: https://registry.example.com/${LONG}/manifests/sha256:${SHA_64}`,
      detail: json({ arn: ARN_200 }),
    },
  ],
  releaseNotes: null,
  approvalHistory: DETAIL.approvalHistory.map((e) => ({
    ...e,
    actorDisplay: `${LONG}@example.com`,
    reason: e.reason && `${e.reason} ${LONG}`,
  })),
};

/** Mid-rollout: the rollout step pulses and the clock runs. */
export const DETAIL_DEPLOYING: DeploymentDetailScreenProps = {
  ...DETAIL,
  deployment: {
    ...DEPLOY_DEPLOYING,
    phases: [...OBSERVED_PHASES.slice(0, 3), phase("rollout", 40)],
  },
  log: LOG.slice(0, 2).map((e) => ({
    ...e,
    phase: "",
    event: "",
    deploymentId: DEPLOY_DEPLOYING.id,
  })),
  releaseNotes: null,
  approvalHistory: [],
};

/** The build failed; no later phase timing was observed. */
export const DETAIL_BUILD_FAILED: DeploymentDetailScreenProps = {
  ...DETAIL,
  deployment: {
    ...DEPLOY_FAILED,
    phases: [phase("build", 42, 41, true)],
    imageDigest: "",
    statusReason: "",
    buildError:
      'error: failed to solve: process "/bin/sh -c npm ci" did not complete successfully: exit code: 1\nnpm ERR! code ERESOLVE',
  },
  log: [],
  manifest: null,
};

/** The deployment query failed outright. */
export const DETAIL_ERROR: DeploymentDetailScreenProps = {
  ...DETAIL,
  deployment: null,
  loading: false,
  error: { message: "Network error: failed to fetch" },
};
