// Types matching astrolift_registry/schema/types.py

export type AstroliftGuid = string;

export type ProvisioningStatus =
  | "pending"
  | "provisioning"
  | "ready"
  | "failed";

export type SourceKind =
  | "github"
  | "gitlab"
  | "bitbucket"
  | "gitea"
  | "git_url";

export type TriggerMode = "auto_on_push" | "manual" | "external_ci";

export type WorkloadKind = "deployment" | "statefulset" | "job" | "cronjob";

export interface AstroliftRegisteredApp {
  id: AstroliftGuid;
  slug: string;
  name: string;
  description: string;
  organizationSlug: string;
  teamSlug: string;
  projectSlug: string;
  sourceKind: SourceKind;
  sourceRepo: string;
  sourceUrl: string;
  manifestPath: string;
  defaultBranch: string;
  manifestHash: string;
  registryRepoUri: string;
  k8sNamespace: string;
  subdomain: string;
  isActive: boolean;
  provisioningStatus: ProvisioningStatus;
  provisioningError: string;
  deployTokenLast4: string;
  logRetentionDays: number;
  previewMaxActive: number;
  previewEnabled: boolean;
  triggerMode: TriggerMode;
  deployBranch: string;
  createdAt: string;
  updatedAt: string;
  deletedAt: string | null;
}

export interface AstroliftWorkload {
  id: AstroliftGuid;
  slug: string;
  name: string;
  kind: WorkloadKind;
  isPublic: boolean;
  schedule: string;
  replicas: number;
  cpuRequest: string;
  cpuLimit: string;
  memoryRequest: string;
  memoryLimit: string;
  hpaMinReplicas: number | null;
  hpaMaxReplicas: number | null;
  hpaTargetCpuPct: number;
  storageClass: string;
  storageSize: string;
  registeredAppSlug: string;
}

export interface AstroliftContainer {
  id: AstroliftGuid;
  name: string;
  isPrimary: boolean;
  imageRef: string;
  dockerfilePath: string;
  buildContext: string;
  port: number;
  command: string[];
  args: string[];
  env: Record<string, string>;
  healthcheckKind: "none" | "http" | "tcp" | "exec";
  healthcheckValue: string;
  healthcheckPort: number | null;
  workloadSlug: string;
}
