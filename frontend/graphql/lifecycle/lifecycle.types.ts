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
