export type AstroliftGuid = string;

export type DeploymentStatus =
  | "pending_approval"
  | "pending"
  | "deploying"
  | "running"
  | "redeploying"
  | "failed"
  | "superseded"
  | "rolled_back";

export type TriggerKind =
  | "push"
  | "manual"
  | "ci"
  | "scheduled"
  | "rollback"
  | "promotion";

export type PreviewStatus = "building" | "running" | "failed" | "torn_down";

export interface AstroliftAppEnvironment {
  id: AstroliftGuid;
  name: string;
  url: string;
  deploysPaused: boolean;
  requiredApprovals: number;
  registeredAppSlug: string;
  clusterSlug: string | null;
  domainZone: string | null;
  createdAt: string;
}

export interface AstroliftDeployment {
  id: AstroliftGuid;
  registeredAppSlug: string;
  environmentName: string;
  workloadSlug: string | null;
  triggerKind: TriggerKind;
  status: DeploymentStatus;
  imageTag: string;
  imageDigest: string;
  clusterRevision: string;
  approvalsRequired: number;
  approvalsReceived: number;
  startedAt: string | null;
  succeededAt: string | null;
  failedAt: string | null;
  endedAt: string | null;
  durationSeconds: number | null;
  createdAt: string;
}

export interface AstroliftDeploymentLogEntry {
  id: AstroliftGuid;
  deploymentId: string;
  status: string;
  message: string;
  detail: Record<string, unknown>;
  occurredAt: string;
}

export interface AstroliftDeploymentMetrics {
  windowDays: number;
  total: number;
  succeeded: number;
  failed: number;
  rolledBack: number;
  inFlight: number;
  successRate: number; // 0..1, or -1 when total==0
  meanDurationSeconds: number | null;
  p95DurationSeconds: number | null;
}

export interface AstroliftAppHealthSummary {
  appSlug: string;
  appName: string;
  environmentCount: number;
  latestDeploymentStatus: DeploymentStatus | null;
  latestImageTag: string;
  lastDeployedAt: string | null;
  hasRecentFailure: boolean;
}

export interface AstroliftPreviewEnvironment {
  id: AstroliftGuid;
  registeredAppSlug: string;
  prNumber: number;
  branch: string;
  commitSha: string;
  status: PreviewStatus;
  hostname: string;
  namespace: string;
  lastDeployedAt: string | null;
  tornDownAt: string | null;
}
