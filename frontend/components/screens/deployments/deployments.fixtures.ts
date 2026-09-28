import { fakeController } from "@/components/data-table/fixtures";
import type { CursorTableController, RowSelection } from "@/components/data-table";
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
import type { StartDeploymentSheetProps } from "./StartDeploymentSheet";

/** Hand-typed fixtures for /deployments, /deployments/[id] and the start sheet. */

export { LONG };

const noop = () => {};
const asyncNoop = async () => {};
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

/** The generated JSON scalar is typed Record<string, unknown>; real values are any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

// ---------------------------------------------------------------- list

export function selectionOf(ids: string[]): RowSelection {
  const set = new Set(ids);
  return {
    selectedIds: ids,
    selectedCount: ids.length,
    isSelected: (id) => set.has(id),
    toggle: noop,
    togglePage: noop,
    pageSelectionState: (page) => {
      const on = page.filter((id) => set.has(id)).length;
      if (on === 0) return false;
      return on === page.length ? true : "indeterminate";
    },
    clear: noop,
  };
}

export const TRIGGERED_BY_OTHER: AstroliftDeployment = {
  ...DEPLOY_PENDING_APPROVAL,
  triggerKind: "manual",
  triggeredByMe: false,
};

const ROWS: AstroliftDeployment[] = [
  DEPLOY_DEPLOYING,
  TRIGGERED_BY_OTHER,
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

export function listProps(
  overrides: Partial<DeploymentsScreenProps> = {},
  controller: Partial<CursorTableController<AstroliftDeployment>> = {}
): DeploymentsScreenProps {
  return {
    tab: "active",
    setTab: noop,
    table: fakeController<AstroliftDeployment>({
      rows: ROWS,
      totalCount: ROWS.length,
      sortEnabled: false,
      ...controller,
    }),
    tabCounts: {
      active: { totalCount: 4 },
      previews: { totalCount: 2 },
      pending: { totalCount: 1 },
      history: { totalCount: 38 },
    },
    selection: selectionOf([]),
    selectedDeploys: [],
    canDeploy: true,
    canApprove: true,
    canRollback: true,
    hasAnyAction: true,
    busy: false,
    bulkRunning: false,
    runAction: asyncNoop,
    runBulk: asyncNoop,
    renderStartDialog: () => null,
    ...overrides,
  };
}

export const LIST_LONG_ROWS: AstroliftDeployment[] = [
  { ...DEPLOY_LONG, registeredAppSlug: LONG },
  { ...DEPLOY_RUNNING, registeredAppSlug: LONG, workloadSlug: LONG, imageTag: `sha-${LONG}` },
];

export { DEPLOYMENTS as HISTORY_ROWS };

// ---------------------------------------------------------------- start sheet

const ENVIRONMENTS: AstroliftAppEnvironment[] = [
  {
    id: "env-prod",
    name: "prod",
    registeredAppSlug: "storefront",
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
    createdAt: minutesAgo(60 * 24 * 30),
    deploysPaused: true,
    ingressPaused: false,
    requiredApprovals: 0,
    settings: [],
    url: "https://stg.storefront.example.com",
  },
];

export const START: StartDeploymentSheetProps = {
  open: true,
  onOpenChange: noop,
  apps: [
    { id: "app-1", slug: "storefront", name: "Storefront" },
    { id: "app-2", slug: "billing", name: "Billing" },
  ],
  environments: ENVIRONMENTS,
  appSlug: "storefront",
  setAppSlug: noop,
  submitting: false,
  onSubmit: async () => true,
};

export const START_LONG: StartDeploymentSheetProps = {
  ...START,
  apps: [{ id: "app-long", slug: LONG, name: `${LONG} ${LONG}` }],
  environments: [{ ...ENVIRONMENTS[0], name: `preview-${LONG}` }],
  appSlug: LONG,
};

// ---------------------------------------------------------------- detail

const LOG: AstroliftDeploymentLogEntry[] = [
  {
    id: "fl1",
    deploymentId: DEPLOY_RUNNING.id,
    occurredAt: minutesAgo(42),
    status: "pending",
    message: "Deployment queued.",
    detail: {},
  },
  {
    id: "fl2",
    deploymentId: DEPLOY_RUNNING.id,
    occurredAt: minutesAgo(41),
    status: "deploying",
    message: "Applying manifests to prod-west.",
    detail: json({ replicas: 3, strategy: "rolling" }),
  },
  {
    id: "fl3",
    deploymentId: DEPLOY_RUNNING.id,
    occurredAt: minutesAgo(39),
    status: "running",
    message: "3 of 3 replicas ready.",
    detail: {},
  },
];

export const DETAIL: DeploymentDetailScreenProps = {
  deployment: { ...DEPLOY_RUNNING, approvalsRequired: 1, approvalsReceived: 1 },
  loading: false,
  log: LOG,
  logLoading: false,
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
  deployment: DEPLOY_FAILED,
  log: [
    ...LOG.slice(0, 2),
    {
      id: "fl4",
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
    commitMessage: `${LONG} ${LONG}\n\n${LONG}`,
    ciRunUrl: `https://ci.example.com/${LONG}/runs/1`,
  },
  log: LOG.map((e) => ({ ...e, message: `${e.message} ${LONG}` })),
  releaseNotes: null,
  approvalHistory: DETAIL.approvalHistory.map((e) => ({
    ...e,
    actorDisplay: `${LONG}@example.com`,
    reason: e.reason && `${e.reason} ${LONG}`,
  })),
};
