import type {
  AstroliftAppEnvironment,
  AstroliftDeployment,
} from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import type { EnvironmentControlsViewProps, WorkloadOpsRowViewProps } from "./ControlsSection";
import type { DeployTokenControlViewProps } from "./DeployTokenControl";
import type { DeploymentPanelViewProps } from "./DeploymentPanel";
import type { PendingDeploymentsViewProps } from "./PendingDeployments";
import type { DeployToken } from "./use-deploy-token";

/** Hand-typed fixtures for the app controls group (controls, deploy token, deployments). */

const noop = async () => {};
const yes = async () => true;

export const LONG =
  "payments-reconciliation-worker-for-the-shared-production-tenant-with-a-deliberately-long-name";

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

// ---------------------------------------------------------------- environments

export const ENV_PROD: AstroliftAppEnvironment = {
  id: "env-prod",
  name: "prod",
  registeredAppSlug: "storefront",
  kind: "production",
  region: "us-west-2",
  ownedByMe: false,
  clusterId: "c0ffee00-0000-4000-8000-000000000001",
  clusterSlug: "prod-west",
  clusterProviderPluginSlug: "aws",
  createdAt: "2026-08-02T17:30:00Z",
  deploysPaused: false,
  ingressPaused: false,
  domainZone: "astrolift.app",
  requiredApprovals: 2,
  settings: [],
  url: "https://storefront.astrolift.app",
};

export const ENV_STG: AstroliftAppEnvironment = {
  ...ENV_PROD,
  id: "env-stg",
  name: "stg",
  deploysPaused: true,
  requiredApprovals: 0,
  url: "https://storefront-stg.astrolift.app",
};

export const ENV_LONG: AstroliftAppEnvironment = {
  ...ENV_PROD,
  id: "env-long",
  name: `preview-${LONG}`,
  requiredApprovals: 12,
};

// ---------------------------------------------------------------- workloads

export const WORKLOAD_WEB: AstroliftWorkload = {
  id: "wl-web",
  name: "Web",
  slug: "web",
  kind: "deployment",
  registeredAppSlug: "storefront",
  replicas: 3,
  hpaMinReplicas: null,
  hpaMaxReplicas: null,
  hpaTargetCpuPct: 0,
  cpuRequest: "250m",
  cpuLimit: "1",
  memoryRequest: "256Mi",
  memoryLimit: "1Gi",
  concurrencyPolicy: "",
  schedule: "",
  isPublic: true,
  inClusterServiceFqdn: "web.storefront-prod.svc.cluster.local",
  storageClass: "",
  storageSize: "",
  ownedByMe: false,
  volumes: [] as unknown as AstroliftWorkload["volumes"],
};

export const WORKLOAD_WORKER: AstroliftWorkload = {
  ...WORKLOAD_WEB,
  id: "wl-worker",
  name: "Worker",
  slug: "worker",
  replicas: 0,
  isPublic: false,
  inClusterServiceFqdn: "worker.storefront-prod.svc.cluster.local",
};

export const WORKLOAD_LONG: AstroliftWorkload = {
  ...WORKLOAD_WEB,
  id: "wl-long",
  name: LONG,
  slug: LONG,
  replicas: 20,
};

export const WORKLOADS: AstroliftWorkload[] = [WORKLOAD_WEB, WORKLOAD_WORKER];

// ---------------------------------------------------------------- controls section

export function envControls(
  env: AstroliftAppEnvironment
): Omit<EnvironmentControlsViewProps, "workloads" | "workloadsLoading" | "renderWorkload"> {
  return {
    env,
    lastTag: "sha-4f2a9c1",
    pausing: false,
    resuming: false,
    deploying: false,
    rebuilding: false,
    onTogglePause: noop,
    onDeploy: yes,
    onRebuildAndDeploy: noop,
  };
}

export function workloadOps(envName: string, workload: AstroliftWorkload): WorkloadOpsRowViewProps {
  return {
    envName,
    workload,
    restarting: false,
    scaling: false,
    onRestart: noop,
    onApply: yes,
  };
}

// ---------------------------------------------------------------- deploy token

export const TOKEN: DeployToken = {
  id: "tok-1",
  name: "default",
  last4: "9f3c",
  scopes: ["deploy"],
  isRevoked: false,
  lastUsedAt: "2026-09-27T14:02:00Z",
  lastRotatedAt: null,
  createdAt: "2026-08-02T17:30:00Z",
};

export const DEPLOY_TOKEN: DeployTokenControlViewProps = {
  loading: false,
  active: TOKEN,
  reveal: null,
  onDismissReveal: () => {},
  creating: false,
  rotating: false,
  revoking: false,
  onCreate: noop,
  onRotate: noop,
  onRevoke: noop,
};

// ---------------------------------------------------------------- deployments

export const DEPLOYMENT: AstroliftDeployment = {
  id: "d3b07384-d9a0-4c9b-8f1e-000000000001",
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
  prNumber: 0,
  prUrl: "",
  ciProvider: "github",
  ciActorKind: "user",
  ciRunUrl: "",
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

export const LAST_GOOD: AstroliftDeployment = {
  ...DEPLOYMENT,
  id: "d3b07384-d9a0-4c9b-8f1e-000000000000",
  imageTag: "sha-91be02d4aa",
  createdAt: minutesAgo(60 * 26),
};

export const DEPLOYMENT_PANEL: DeploymentPanelViewProps = {
  loading: false,
  current: DEPLOYMENT,
  lastGood: undefined,
  rolling: false,
  onRollback: noop,
};

export const DEPLOYMENT_FAILED: DeploymentPanelViewProps = {
  ...DEPLOYMENT_PANEL,
  current: {
    ...DEPLOYMENT,
    status: "failed",
    failedAt: minutesAgo(3),
    endedAt: minutesAgo(3),
    succeededAt: null,
  },
  lastGood: LAST_GOOD,
};

export const DEPLOYMENT_IN_PROGRESS: DeploymentPanelViewProps = {
  ...DEPLOYMENT_PANEL,
  current: {
    ...DEPLOYMENT,
    status: "deploying",
    startedAt: minutesAgo(2),
    endedAt: null,
    succeededAt: null,
  },
};

// ---------------------------------------------------------------- pending approval

export const PENDING_DEPLOYMENT: AstroliftDeployment = {
  ...DEPLOYMENT,
  id: "d3b07384-d9a0-4c9b-8f1e-000000000002",
  imageTag: "sha-c81d5e2f90",
  status: "pending_approval",
  triggerKind: "ci",
  approvalsReceived: 1,
  approvalsRequired: 2,
  createdAt: minutesAgo(8),
  startedAt: null,
  endedAt: null,
  succeededAt: null,
};

export const PENDING: PendingDeploymentsViewProps = {
  loading: false,
  pending: [
    PENDING_DEPLOYMENT,
    {
      ...PENDING_DEPLOYMENT,
      id: "d3b07384-d9a0-4c9b-8f1e-000000000003",
      imageTag: "sha-0a7f3b6e12",
      environmentName: "stg",
      approvalsReceived: 0,
      approvalsRequired: 0,
      triggerKind: "manual",
    },
  ],
  canApprove: true,
  onApprove: noop,
  onReject: noop,
};
