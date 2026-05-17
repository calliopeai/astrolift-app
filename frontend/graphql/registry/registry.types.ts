/**
 * Registry types — facade over the codegen output.
 */

import type {
  AstroliftAppTeamAccess as GeneratedAppTeamAccess,
  AstroliftContainer as GeneratedContainer,
  AstroliftRegisteredApp as GeneratedRegisteredApp,
  AstroliftWorkload as GeneratedWorkload,
} from "@/graphql/__generated__/schema";

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

export type TriggerMode =
  | "auto_on_push"
  | "manual"
  | "external_ci"
  | "cron";

export type WorkloadKind = "deployment" | "statefulset" | "job" | "cronjob";

export type ManifestSyncState =
  | "in_sync"
  | "db_ahead"
  | "repo_ahead"
  | "diverged";

export type HealthcheckKind = "none" | "http" | "tcp" | "exec";

export type AstroliftRegisteredApp = Omit<
  GeneratedRegisteredApp,
  "sourceKind" | "provisioningStatus" | "triggerMode" | "manifestSyncState"
> & {
  sourceKind: SourceKind;
  provisioningStatus: ProvisioningStatus;
  triggerMode: TriggerMode;
  manifestSyncState: ManifestSyncState;
};

export type AstroliftWorkload = Omit<GeneratedWorkload, "kind"> & {
  kind: WorkloadKind;
  /**
   * In-cluster DNS name for the workload's ClusterIP Service —
   * ``<slug>.<namespace>.svc.cluster.local`` (#429). Populated by
   * the backend; empty string when the namespace half can't be
   * computed (e.g. orphan app rows). Codegen will pick this up on
   * the next ``make codegen`` run; the manual entry keeps the
   * workload detail page typesafe until then.
   */
  inClusterServiceFqdn: string;
};

export type AstroliftContainer = Omit<GeneratedContainer, "healthcheckKind"> & {
  healthcheckKind: HealthcheckKind;
};

export type AppTeamAccessLevel = "viewer" | "deployer" | "owner";

export type AstroliftAppTeamAccess = Omit<
  GeneratedAppTeamAccess,
  "accessLevel"
> & {
  accessLevel: AppTeamAccessLevel;
};
