import type {
  AstroliftAppLogLine,
  AstroliftDeployment,
  AstroliftDeploymentComparison,
} from "@/graphql/lifecycle/lifecycle.types";

import type { AppDeploymentsScreenProps } from "./AppDeploymentsScreen";
import type { AppLogsScreenProps } from "./AppLogsScreen";
import type { CompareDeploymentsSheetViewProps } from "./CompareDeploymentsSheet";
import type { DeploymentActions } from "./DeploymentRowActions";

/** Hand-typed fixtures for the app deployments and logs tabs. */

const noop = () => {};
const asyncNoop = async () => {};

export const LONG =
  "payments-reconciliation-worker-for-the-shared-production-tenant-with-a-deliberately-long-name";

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

export const APP = { name: "Storefront", slug: "storefront", sourceRepo: "example/storefront" };

// ---------------------------------------------------------------- deployments

export const DEPLOY_RUNNING: AstroliftDeployment = {
  id: "d3b07384-d9a0-4c9b-8f1e-000000000010",
  registeredAppSlug: "storefront",
  environmentName: "prod",
  workloadSlug: "web",
  imageTag: "sha-4f2a9c1e7b",
  imageDigest: "sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef",
  status: "running",
  statusReason: "",
  strategy: "rolling",
  triggerKind: "push",
  branch: "main",
  commitSha: "4f2a9c1e7b3d",
  commitMessage: "Tighten checkout retry budget",
  commitAuthor: "leo",
  commitAuthorAvatarUrl: "",
  repoUrl: "https://github.com/example/storefront",
  prNumber: 412,
  prUrl: "https://github.com/example/storefront/pull/412",
  ciProvider: "github",
  ciActorKind: "user",
  ciRunUrl: "https://github.com/example/storefront/actions/runs/1",
  clusterRevision: "42",
  buildError: "",
  abortedReason: "",
  manifestResyncStatus: "",
  manifestResyncError: "",
  approvalsReceived: 0,
  approvalsRequired: 0,
  requiredApproverCount: 0,
  approvedBy: [],
  awaitingApprovers: [],
  triggeredByUserId: null,
  triggeredByMe: false,
  createdAt: minutesAgo(42),
  startedAt: minutesAgo(42),
  endedAt: minutesAgo(39),
  succeededAt: minutesAgo(39),
  failedAt: null,
  durationSeconds: 184,
};

export const DEPLOY_FAILED: AstroliftDeployment = {
  ...DEPLOY_RUNNING,
  id: "d3b07384-d9a0-4c9b-8f1e-000000000009",
  imageTag: "sha-91be02d4aa",
  status: "failed",
  statusReason: "Readiness probe failed on 2 of 3 replicas.",
  buildError: 'error: failed to solve: process "/bin/sh -c npm ci" did not complete',
  createdAt: minutesAgo(60 * 5),
  startedAt: minutesAgo(60 * 5),
  succeededAt: null,
  failedAt: minutesAgo(60 * 5 - 2),
  durationSeconds: 97,
};

export const DEPLOY_PENDING_APPROVAL: AstroliftDeployment = {
  ...DEPLOY_RUNNING,
  id: "d3b07384-d9a0-4c9b-8f1e-000000000011",
  imageTag: "sha-0c1d2e3f4a",
  status: "pending_approval",
  statusReason: "Waiting on 1 of 2 approvals.",
  approvalsReceived: 1,
  approvalsRequired: 2,
  createdAt: minutesAgo(3),
  startedAt: null,
  endedAt: null,
  succeededAt: null,
  durationSeconds: null,
};

export const DEPLOY_DEPLOYING: AstroliftDeployment = {
  ...DEPLOY_RUNNING,
  id: "d3b07384-d9a0-4c9b-8f1e-000000000012",
  environmentName: "stg",
  imageTag: "sha-7a8b9c0d1e",
  status: "deploying",
  statusReason: "Queued behind deploy sha-4f2a9c1e7b, still deploying.",
  createdAt: minutesAgo(1),
  startedAt: minutesAgo(1),
  endedAt: null,
  succeededAt: null,
  durationSeconds: 41,
};

export const DEPLOY_SUPERSEDED: AstroliftDeployment = {
  ...DEPLOY_RUNNING,
  id: "d3b07384-d9a0-4c9b-8f1e-000000000008",
  imageTag: "sha-5e6f7a8b9c",
  status: "superseded",
  createdAt: minutesAgo(60 * 26),
  startedAt: minutesAgo(60 * 26),
  durationSeconds: 212,
};

export const DEPLOYMENTS: AstroliftDeployment[] = [
  DEPLOY_DEPLOYING,
  DEPLOY_PENDING_APPROVAL,
  DEPLOY_RUNNING,
  DEPLOY_FAILED,
  DEPLOY_SUPERSEDED,
];

export const DEPLOY_LONG: AstroliftDeployment = {
  ...DEPLOY_FAILED,
  id: "d3b07384-d9a0-4c9b-8f1e-000000000099",
  imageTag: `sha-${LONG}`,
  workloadSlug: LONG,
  environmentName: `preview-${LONG}`,
  branch: `feature/${LONG}`,
  commitAuthor: LONG,
  statusReason: `Readiness probe failed: ${LONG} ${LONG}`,
  abortedReason: "",
  buildError: `${LONG}\n`.repeat(8),
  manifestResyncStatus: "stale",
  manifestResyncError: `template render failed at ${LONG}`,
};

// ---------------------------------------------------------------- screen

/** Everything but the list state, which stories build with `useLocalListState`. */
export const SCREEN: Omit<AppDeploymentsScreenProps, "list"> = {
  rows: DEPLOYMENTS,
  loading: false,
  error: null,
  onRetry: noop,
  nextCursor: "cursor-2",
  totalCount: 48,
  lookup: (id) => [...DEPLOYMENTS, DEPLOY_LONG].find((d) => d.id === id) ?? null,
  environmentsHref: "/apps/storefront/environments",
  rowHref: (d) => `#deployment-${d.id}`,
};

// ---------------------------------------------------------------- row actions

export const ACTIONS: DeploymentActions = {
  canApprove: true,
  canDeploy: true,
  canRollback: true,
  busy: false,
  onApprove: asyncNoop,
  onAbort: asyncNoop,
  onRedeploy: asyncNoop,
  onRollback: asyncNoop,
};

// ---------------------------------------------------------------- compare

/** Codegen maps the JSON scalar to an object; a manifest diff value is any JSON. */
export const json = (value: unknown) => value as Record<string, unknown>;

const COMPARISON: AstroliftDeploymentComparison = {
  deploymentAId: DEPLOY_SUPERSEDED.id,
  deploymentBId: DEPLOY_RUNNING.id,
  baseSha: "5e6f7a8b9c0d1e2f",
  headSha: "4f2a9c1e7b3d5a6b",
  compareUrl: "https://github.com/example/storefront/compare/5e6f7a8...4f2a9c1",
  imageDiffSummary: "3 layers changed, +1.2 MB\n/app/dist: 14 files changed",
  manifestDiff: [
    { op: "replace", path: "/Deployment/web/spec/replicas", before: json(2), after: json(3) },
    { op: "add", path: "/ConfigMap/web-flags", before: json(null), after: { CHECKOUT_V2: "true" } },
    { op: "remove", path: "/Service/web-legacy", before: { port: 8080 }, after: json(null) },
  ],
};

export const COMPARE: CompareDeploymentsSheetViewProps = {
  open: true,
  onOpenChange: noop,
  deployA: DEPLOY_SUPERSEDED,
  deployB: DEPLOY_RUNNING,
  comparison: COMPARISON,
  loading: false,
  error: null,
};

// ---------------------------------------------------------------- logs

const POD = "storefront-web-7d9f8b6c4-x2k9p";

const LOG_LINES: AstroliftAppLogLine[] = [
  "GET /healthz 200 1ms",
  "GET /api/cart 200 18ms",
  "WARN slow query on orders (412ms)",
  "ERROR payment provider timeout after 5000ms",
  "GET /api/cart 200 12ms",
].map((message, i) => ({
  container: "web",
  podName: POD,
  stream: message.startsWith("ERROR") ? "stderr" : "stdout",
  timestamp: minutesAgo(5 - i),
  message,
}));

export const LOGS: AppLogsScreenProps = {
  appError: null,
  onRetryApp: () => {},
  slug: "storefront",
  app: APP,
  loading: false,
  streaming: true,
  toggleStreaming: noop,
  lines: LOG_LINES,
  clearLines: noop,
  error: null,
  retry: noop,
  downloadLines: noop,
  podRows: [{ name: POD }, { name: "storefront-web-7d9f8b6c4-q8m1z" }],
  selectedPod: POD,
  setPickedPod: noop,
  podContainers: ["web", "istio-proxy"],
  selectedContainer: "web",
  setPickedContainer: noop,
  podsLoading: false,
  noPods: false,
  logExport: { busy: false, onExport: async () => true },
  deploymentsHref: "/apps/storefront/deployments",
};

export const LOGS_LONG: AppLogsScreenProps = {
  ...LOGS,
  app: { name: LONG, slug: LONG },
  podRows: [{ name: `${LONG}-7d9f8b6c4-x2k9p` }],
  selectedPod: `${LONG}-7d9f8b6c4-x2k9p`,
  podContainers: [LONG],
  selectedContainer: LONG,
  lines: LOG_LINES.map((l) => ({ ...l, message: `${l.message} ${LONG} ${LONG}` })),
};
