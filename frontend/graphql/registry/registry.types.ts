/**
 * Registry types — facade over the codegen output.
 */

import type {
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

export type TriggerMode = "auto_on_push" | "manual" | "external_ci";

export type WorkloadKind = "deployment" | "statefulset" | "job" | "cronjob";

export type HealthcheckKind = "none" | "http" | "tcp" | "exec";

export type AstroliftRegisteredApp = Omit<
  GeneratedRegisteredApp,
  "sourceKind" | "provisioningStatus" | "triggerMode"
> & {
  sourceKind: SourceKind;
  provisioningStatus: ProvisioningStatus;
  triggerMode: TriggerMode;
};

export type AstroliftWorkload = Omit<GeneratedWorkload, "kind"> & {
  kind: WorkloadKind;
};

export type AstroliftContainer = Omit<GeneratedContainer, "healthcheckKind"> & {
  healthcheckKind: HealthcheckKind;
};
