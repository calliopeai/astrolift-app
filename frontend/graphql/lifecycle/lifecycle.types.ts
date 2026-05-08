/**
 * Lifecycle types — facade over the codegen output.
 *
 * Entity types come from `__generated__/schema.ts` so they stay in
 * lockstep with the backend. The narrow string-union types
 * (DeploymentStatus, TriggerKind, PreviewStatus) live here because
 * they're frontend-side switch-exhaustiveness aids — the schema
 * carries them as plain `String!` fields. We override those fields
 * on the generated entity with the narrow unions so consumer code
 * (e.g. STATUS_DOT[d.status]) typechecks against valid values.
 *
 * When the backend grows a new status value, regen the schema and
 * add it to the union here. Codegen will catch a mismatch the next
 * time consumers actually use the field.
 */

import type {
  AstroliftAppEnvironment as GeneratedAppEnvironment,
  AstroliftAppHealthSummary as GeneratedAppHealthSummary,
  AstroliftCommandRun as GeneratedCommandRun,
  AstroliftDeployment as GeneratedDeployment,
  AstroliftDeploymentLogEntry as GeneratedDeploymentLogEntry,
  AstroliftDeploymentMetrics as GeneratedDeploymentMetrics,
  AstroliftPreviewEnvironment as GeneratedPreviewEnvironment,
  AstroliftScheduledJobRun as GeneratedScheduledJobRun,
} from "@/graphql/__generated__/schema";

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

export type AstroliftAppEnvironment = GeneratedAppEnvironment;

export type AstroliftDeployment = Omit<
  GeneratedDeployment,
  "status" | "triggerKind"
> & {
  status: DeploymentStatus;
  triggerKind: TriggerKind;
};

export type AstroliftDeploymentLogEntry = GeneratedDeploymentLogEntry;

export type AstroliftPreviewEnvironment = Omit<
  GeneratedPreviewEnvironment,
  "status"
> & {
  status: PreviewStatus;
};

export type AstroliftDeploymentMetrics = GeneratedDeploymentMetrics;

export type AstroliftAppHealthSummary = Omit<
  GeneratedAppHealthSummary,
  "latestDeploymentStatus"
> & {
  latestDeploymentStatus: DeploymentStatus | null;
};

export type ScheduledJobRunStatus =
  | "running"
  | "succeeded"
  | "failed"
  | "superseded";

export type AstroliftScheduledJobRun = Omit<
  GeneratedScheduledJobRun,
  "status"
> & {
  status: ScheduledJobRunStatus;
};

export type AstroliftCommandRun = GeneratedCommandRun;
